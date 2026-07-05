#include <Servo.h>
#include <Adafruit_NeoPixel.h>

// ============================================================
// NeoPixels
// Front two pixels on D4
// Rear two pixels on D5
// ============================================================

#define FRONT_PIXEL_PIN 4
#define REAR_PIXEL_PIN  5
#define PIXELS_PER_STRIP 2

Adafruit_NeoPixel frontPixels(
  PIXELS_PER_STRIP,
  FRONT_PIXEL_PIN,
  NEO_GRB + NEO_KHZ800
);

Adafruit_NeoPixel rearPixels(
  PIXELS_PER_STRIP,
  REAR_PIXEL_PIN,
  NEO_GRB + NEO_KHZ800
);

#define CLR_OFF    0x000000UL
#define CLR_GREEN  0x00FF00UL
#define CLR_WHITE  0xFFFFFFUL
#define CLR_RED    0xFF0000UL
#define CLR_YELLOW 0xFFFF00UL

void showPixels(uint32_t color) {
  for (int i = 0; i < PIXELS_PER_STRIP; i++) {
    frontPixels.setPixelColor(i, color);
    rearPixels.setPixelColor(i, color);
  }

  frontPixels.show();
  rearPixels.show();
}

// ============================================================
// Pan motor driver
//
// D2 = IN1
// D7 = IN2
// D9 = PWM / enable
// D6 = home switch
//
// Home-switch wiring:
// NC     -> 5 V
// NO     -> GND
// Common -> D6
//
// Released = HIGH
// Pressed  = LOW
// ============================================================

const int PAN_IN1_PIN = 2;
const int PAN_IN2_PIN = 7;
const int PAN_PWM_PIN = 9;
const int PAN_HOME_PIN = 6;

const int PAN_HOME_SPEED = -30;
const int PAN_MIN_PWM = 35;

const unsigned long PAN_HOME_TIMEOUT_MS = 30000;

int panSpeed = 0;
bool panHomed = false;

// ============================================================
// Tilt servo
// ============================================================

const int TILT_PIN = 3;

const int TILT_MIN = 0;
const int TILT_MAX = 180;

Servo tiltServo;

int tiltAngle = 90;

// ============================================================
// Battery voltage
// ============================================================

const int BATTERY_PIN = A0;

const float ADC_REFERENCE_VOLTAGE = 5.0;
const float ADC_MAX_VALUE = 1023.0;
const float BATTERY_DIVIDER_RATIO = 5.0;
const float BATTERY_CALIBRATION = 1.000;

const int BATTERY_SAMPLE_COUNT = 16;

const unsigned long BATTERY_REPORT_INTERVAL_MS = 5000;
unsigned long lastBatteryReportTime = 0;

// ============================================================
// Serial input
// ============================================================

String inputLine = "";

// ============================================================
// Setup
// ============================================================

void setup() {
  Serial.begin(115200);
  delay(1500);

  // Pan motor driver
  pinMode(PAN_IN1_PIN, OUTPUT);
  pinMode(PAN_IN2_PIN, OUTPUT);
  pinMode(PAN_PWM_PIN, OUTPUT);

  stopPan();

  // Pan home switch
  pinMode(PAN_HOME_PIN, INPUT_PULLUP);

  // Tilt servo
  tiltServo.attach(TILT_PIN);
  tiltServo.write(tiltAngle);

  // NeoPixels
  frontPixels.begin();
  rearPixels.begin();

  frontPixels.setBrightness(80);
  rearPixels.setBrightness(80);

  frontPixels.clear();
  rearPixels.clear();

  frontPixels.show();
  rearPixels.show();

  showPixels(CLR_GREEN);

  // Battery input
  pinMode(BATTERY_PIN, INPUT);
  analogRead(BATTERY_PIN);

  Serial.println("READY TURRET_NANO");
  Serial.println("CMDS: P-100..P100 T0..T180 PTspeed,tilt H STOP ? B");

  reportBatteryVoltage();

  lastBatteryReportTime = millis();
}

// ============================================================
// Main loop
// ============================================================

void loop() {
  handleSerialInput();
  handleBatteryStreaming();
}

// ============================================================
// Serial handling
// ============================================================

void handleSerialInput() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      inputLine.trim();

      if (inputLine.length() > 0) {
        handleCommand(inputLine);
      }

      inputLine = "";
    } else {
      inputLine += c;

      if (inputLine.length() > 40) {
        inputLine = "";
        Serial.println("ERR CMD_TOO_LONG");
      }
    }
  }
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "?") {
    reportState();
    reportBatteryVoltage();
    return;
  }

  if (cmd == "B") {
    reportBatteryVoltage();
    return;
  }

  if (cmd == "STOP") {
    stopPan();
    Serial.println("OK PAN_STOP");
    return;
  }

  if (cmd == "H" || cmd == "HOME") {
    homePan();
    return;
  }

  // Pan speed and tilt together:
  // PT-30,45
  if (cmd.startsWith("PT")) {
    String values = cmd.substring(2);
    int commaIndex = values.indexOf(',');

    if (commaIndex == -1) {
      Serial.println("ERR BAD_PT_FORMAT");
      return;
    }

    String panValue = values.substring(0, commaIndex);
    String tiltValue = values.substring(commaIndex + 1);

    panValue.trim();
    tiltValue.trim();

    if (!isValidNumber(panValue, -100, 100)) {
      Serial.println("ERR BAD_PAN_SPEED");
      return;
    }

    if (!isValidNumber(tiltValue, TILT_MIN, TILT_MAX)) {
      Serial.println("ERR BAD_TILT");
      return;
    }

    setPanSpeed(panValue.toInt());
    setTilt(tiltValue.toInt());

    Serial.print("OK PAN_SPEED ");
    Serial.print(panSpeed);
    Serial.print(" TILT ");
    Serial.println(tiltAngle);

    return;
  }

  // Pan speed:
  // P-100 through P100
  if (cmd.startsWith("P")) {
    String value = cmd.substring(1);
    value.trim();

    if (!isValidNumber(value, -100, 100)) {
      Serial.println("ERR BAD_PAN_SPEED");
      return;
    }

    setPanSpeed(value.toInt());

    Serial.print("OK PAN_SPEED ");
    Serial.println(panSpeed);

    return;
  }

  // Tilt angle:
  // T0 through T180
  if (cmd.startsWith("T")) {
    String value = cmd.substring(1);
    value.trim();

    if (!isValidNumber(value, TILT_MIN, TILT_MAX)) {
      Serial.println("ERR BAD_TILT");
      return;
    }

    setTilt(value.toInt());

    Serial.print("OK TILT ");
    Serial.println(tiltAngle);

    return;
  }

  Serial.println("ERR BAD_CMD");
}

// ============================================================
// Pan motor functions
// ============================================================

void setPanSpeed(int speedValue) {
  speedValue = constrain(speedValue, -100, 100);
  panSpeed = speedValue;

  if (panSpeed == 0) {
    stopPan();
    return;
  }

  int pwmValue = map(
    abs(panSpeed),
    1,
    100,
    PAN_MIN_PWM,
    255
  );

  analogWrite(PAN_PWM_PIN, 0);

  if (panSpeed > 0) {
    digitalWrite(PAN_IN1_PIN, HIGH);
    digitalWrite(PAN_IN2_PIN, LOW);
  } else {
    digitalWrite(PAN_IN1_PIN, LOW);
    digitalWrite(PAN_IN2_PIN, HIGH);
  }

  analogWrite(PAN_PWM_PIN, pwmValue);
}

void stopPan() {
  panSpeed = 0;

  analogWrite(PAN_PWM_PIN, 0);

  digitalWrite(PAN_IN1_PIN, LOW);
  digitalWrite(PAN_IN2_PIN, LOW);
}

// ============================================================
// Pan homing
// ============================================================

void homePan() {
  Serial.println("HOME START");
  showPixels(CLR_WHITE);

  if (homeSwitchPressed()) {
    stopPan();
    panHomed = true;

    showPixels(CLR_GREEN);
    Serial.println("HOME OK SWITCH_ALREADY_PRESSED");
    return;
  }

  panHomed = false;

  unsigned long startTime = millis();

  setPanSpeed(PAN_HOME_SPEED);

  while (!homeSwitchPressed()) {
    if (millis() - startTime >= PAN_HOME_TIMEOUT_MS) {
      stopPan();

      showPixels(CLR_RED);
      Serial.println("ERR HOME_TIMEOUT");
      return;
    }

    delay(2);
  }

  stopPan();
  delay(30);

  if (!homeSwitchPressed()) {
    showPixels(CLR_YELLOW);
    Serial.println("ERR HOME_SWITCH_BOUNCE");
    return;
  }

  panHomed = true;

  showPixels(CLR_GREEN);
  Serial.println("HOME OK PAN_ZERO");
}

bool homeSwitchPressed() {
  return digitalRead(PAN_HOME_PIN) == LOW;
}

// ============================================================
// Tilt servo
// ============================================================

void setTilt(int angle) {
  tiltAngle = constrain(angle, TILT_MIN, TILT_MAX);
  tiltServo.write(tiltAngle);
}

// ============================================================
// State reporting
// ============================================================

void reportState() {
  Serial.print("STATE PAN_SPEED ");
  Serial.print(panSpeed);

  Serial.print(" PAN_HOMED ");
  Serial.print(panHomed ? "YES" : "NO");

  Serial.print(" HOME_SWITCH ");
  Serial.print(homeSwitchPressed() ? "PRESSED" : "RELEASED");

  Serial.print(" TILT ");
  Serial.println(tiltAngle);
}

// ============================================================
// Battery voltage
// ============================================================

void handleBatteryStreaming() {
  unsigned long currentTime = millis();

  if (
    currentTime - lastBatteryReportTime >=
    BATTERY_REPORT_INTERVAL_MS
  ) {
    lastBatteryReportTime = currentTime;
    reportBatteryVoltage();
  }
}

float readBatteryVoltage() {
  unsigned long adcTotal = 0;

  for (int i = 0; i < BATTERY_SAMPLE_COUNT; i++) {
    adcTotal += analogRead(BATTERY_PIN);
    delayMicroseconds(500);
  }

  float averageAdc =
    (float)adcTotal / (float)BATTERY_SAMPLE_COUNT;

  float voltageAtPin =
    averageAdc *
    ADC_REFERENCE_VOLTAGE /
    ADC_MAX_VALUE;

  return
    voltageAtPin *
    BATTERY_DIVIDER_RATIO *
    BATTERY_CALIBRATION;
}

void reportBatteryVoltage() {
  Serial.print("BATTERY ");
  Serial.print(readBatteryVoltage(), 2);
  Serial.println(" V");
}

// ============================================================
// Validation
// ============================================================

bool isValidNumber(
  String value,
  int minimum,
  int maximum
) {
  value.trim();

  if (value.length() == 0) {
    return false;
  }

  int firstDigit = 0;

  if (
    value.charAt(0) == '-' ||
    value.charAt(0) == '+'
  ) {
    if (value.length() == 1) {
      return false;
    }

    firstDigit = 1;
  }

  for (unsigned int i = firstDigit; i < value.length(); i++) {
    if (!isDigit(value.charAt(i))) {
      return false;
    }
  }

  long number = value.toInt();

  return number >= minimum && number <= maximum;
}