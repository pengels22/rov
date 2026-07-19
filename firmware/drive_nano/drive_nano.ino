// ============================================================
//  drive_nano.ino  —  ROV drive controller (Arduino Nano ESP32)
//  Requires: Adafruit NeoPixel library
//
//  Serial protocol:
//    Host → Nano : HB
//    Nano → Host : ACK
//    Host → Nano : <dir>,<dist_in>[,<speed>]   e.g. fwd,12 | fwd,-12 | fwd,12,180 | fwd,0
//    Nano → Host : ACK
//
//    stop, STATUS, error<n>=true may be sent at any time without HB
//
//  TB6612-style direction + PWM outputs.
// ============================================================

#include <Preferences.h>

Preferences prefs;

// =========================
// Pin map
// =========================
#define ENC_L_A     2
#define ENC_L_B     3
#define ENC_R_A     4
#define ENC_R_B     5

#define MOTOR_LEFT_FWD_PIN    8   // Left forward
#define MOTOR_LEFT_REV_PIN    9   // Left backward
#define MOTOR_RIGHT_FWD_PIN   6   // Right forward
#define MOTOR_RIGHT_REV_PIN   7   // Right backward
#define MOTOR_LEFT_PWM_PIN    12  // Left PWM
#define MOTOR_RIGHT_PWM_PIN   11  // Right PWM

#define BATT_PIN    A0

// =========================
// Calibration
// =========================
#define BATT_DIVIDER     5.0f
#define WATCHDOG_MS      2000


// =========================
// Drive state
// =========================
enum DriveMode : uint8_t {
  MODE_STOP,
  MODE_FWD,
  MODE_REV,
  MODE_LEFT,
  MODE_RIGHT
};

enum LedMode : uint8_t {
  LED_MODE_AUTO,
  LED_MODE_GREEN
};

DriveMode oppositeMode(DriveMode mode) {
  switch (mode) {
    case MODE_FWD:
      return MODE_REV;
    case MODE_REV:
      return MODE_FWD;
    case MODE_LEFT:
      return MODE_RIGHT;
    case MODE_RIGHT:
      return MODE_LEFT;
    case MODE_STOP:
    default:
      return MODE_STOP;
  }
}

volatile long encL = 0;
volatile long encR = 0;

// Adjust these when the physical wiring or mounting orientation changes.
const int ENC_L_SIGN = -1;
const int ENC_R_SIGN = -1;

DriveMode currentMode = MODE_STOP;
bool motorsActive = false;

long targetTicks = 0;
bool useDistance = false;

int ticksPerInch = 100;
bool calMode = false;

int activeError = 0;
bool waitForMove = false;

int currentTurn = 0;
LedMode ledMode = LED_MODE_AUTO;

unsigned long lastHbMs = 0;
unsigned long lastStreamMs = 0;
unsigned long streamInterval = 0;
int lastLeftPwm = 0;
int lastRightPwm = 0;

// =========================
// Encoder ISRs
// =========================
void IRAM_ATTR isrLeftA() {
  if (digitalRead(ENC_L_A) == digitalRead(ENC_L_B)) encL += ENC_L_SIGN;
  else encL -= ENC_L_SIGN;
}

void IRAM_ATTR isrRightA() {
  if (digitalRead(ENC_R_A) == digitalRead(ENC_R_B)) encR += ENC_R_SIGN;
  else encR -= ENC_R_SIGN;
}

// =========================
// Motor helpers
// =========================
// dir: -1 = reverse, 0 = stop, +1 = forward

void motorLeft(int dir, int pwm = 255) {
  pwm = constrain(pwm, 0, 255);

  if (dir > 0) {
    digitalWrite(MOTOR_LEFT_FWD_PIN, HIGH);
    digitalWrite(MOTOR_LEFT_REV_PIN, LOW);
  } else if (dir < 0) {
    digitalWrite(MOTOR_LEFT_FWD_PIN, LOW);
    digitalWrite(MOTOR_LEFT_REV_PIN, HIGH);
  } else {
    digitalWrite(MOTOR_LEFT_FWD_PIN, LOW);
    digitalWrite(MOTOR_LEFT_REV_PIN, LOW);
  }

  if (dir == 0) pwm = 0;
  analogWrite(MOTOR_LEFT_PWM_PIN, pwm);
  lastLeftPwm = pwm;
}

void motorRight(int dir, int pwm = 255) {
  pwm = constrain(pwm, 0, 255);

  if (dir > 0) {
    digitalWrite(MOTOR_RIGHT_FWD_PIN, HIGH);
    digitalWrite(MOTOR_RIGHT_REV_PIN, LOW);
  } else if (dir < 0) {
    digitalWrite(MOTOR_RIGHT_FWD_PIN, LOW);
    digitalWrite(MOTOR_RIGHT_REV_PIN, HIGH);
  } else {
    digitalWrite(MOTOR_RIGHT_FWD_PIN, LOW);
    digitalWrite(MOTOR_RIGHT_REV_PIN, LOW);
  }

  if (dir == 0) pwm = 0;
  analogWrite(MOTOR_RIGHT_PWM_PIN, pwm);
  lastRightPwm = pwm;
}

void applyDrive(int leftSignedPwm, int rightSignedPwm) {
  int leftDir = 0;
  int rightDir = 0;

  if (leftSignedPwm > 0) leftDir = 1;
  else if (leftSignedPwm < 0) leftDir = -1;

  if (rightSignedPwm > 0) rightDir = 1;
  else if (rightSignedPwm < 0) rightDir = -1;

  motorLeft(leftDir, abs(leftSignedPwm));
  motorRight(rightDir, abs(rightSignedPwm));
}

void stopMotors() {
  motorLeft(0, 0);
  motorRight(0, 0);

  currentMode = MODE_STOP;
  motorsActive = false;
  currentTurn = 0;
  useDistance = false;
}

// =========================
// Drive command
// =========================
void drive(DriveMode mode, float distIn, int speed = 255) {
  encL = 0;
  encR = 0;
  currentTurn = 0;
  speed = constrain(speed, 0, 255);

  if (distIn > 0.0f) {
    targetTicks = (long)(distIn * ticksPerInch);
    useDistance = true;
  } else {
    targetTicks = 0;
    useDistance = false;
  }

  currentMode = mode;
  motorsActive = true;

  switch (mode) {
    case MODE_FWD:
      applyDrive(speed, speed);
      break;

    case MODE_REV:
      applyDrive(-speed, -speed);
      break;

    case MODE_RIGHT:
      applyDrive(-speed, speed);
      currentTurn = speed;
      break;

    case MODE_LEFT:
      applyDrive(speed, -speed);
      currentTurn = -speed;
      break;

    default:
      stopMotors();
      break;
  }
}

// =========================
// Joystick / arcade drive
// =========================
void joyDrive(int throttle, int turn) {
  const int T = 20;
  int left = constrain(throttle + turn, -255, 255);
  int right = constrain(throttle - turn, -255, 255);

  if (abs(left) <= T) left = 0;
  if (abs(right) <= T) right = 0;

  currentTurn = turn;

  if (left == 0 && right == 0) {
    stopMotors();
    return;
  }

  motorsActive = true;
  if (left > 0 && right > 0) currentMode = MODE_FWD;
  else if (left < 0 && right < 0) currentMode = MODE_REV;
  else if (left > 0 && right < 0) currentMode = MODE_LEFT;
  else if (left < 0 && right > 0) currentMode = MODE_RIGHT;
  else if (left != 0) currentMode = left > 0 ? MODE_FWD : MODE_REV;
  else currentMode = right > 0 ? MODE_FWD : MODE_REV;

  applyDrive(left, right);
}

// LED helpers removed (NeoPixel moved to turret firmware)

// =========================
// Battery voltage
// =========================
float readBatteryV() {
  int raw = analogRead(BATT_PIN);
  return (raw / 4095.0f) * 3.3f * BATT_DIVIDER;
}

// =========================
// Serial command parser
// =========================
void parseCommand(const String& cmd) {

  // --- Heartbeat ---
  if (cmd == "HB") {
    lastHbMs = millis();
    waitForMove = true;
    Serial.println("ACK");
    return;
  }

  // --- Status ---
  if (cmd == "STATUS") {
    const char* modeStr = "STOP";

    switch (currentMode) {
      case MODE_FWD:
        modeStr = "FWD";
        break;

      case MODE_REV:
        modeStr = "REV";
        break;

      case MODE_LEFT:
        modeStr = "LEFT";
        break;

      case MODE_RIGHT:
        modeStr = "RIGHT";
        break;

      default:
        break;
    }

    Serial.print("{\"mode\":\"");
    Serial.print(modeStr);

    Serial.print("\",\"motors_active\":");
    Serial.print(motorsActive ? "true" : "false");

    Serial.print(",\"enc_l\":");
    Serial.print(encL);

    Serial.print(",\"enc_r\":");
    Serial.print(encR);

    Serial.print(",\"batt_v\":");
    Serial.print(readBatteryV(), 2);

    Serial.print(",\"error\":");
    Serial.print(activeError);

    Serial.print(",\"led_mode\":\"");
    Serial.print(ledMode == LED_MODE_GREEN ? "GREEN" : "AUTO");
    Serial.print("\"");

    Serial.print(",\"ticks_per_inch\":");
    Serial.print(ticksPerInch);

    Serial.print(",\"left_pwm\":");
    Serial.print(lastLeftPwm);

    Serial.print(",\"right_pwm\":");
    Serial.print(lastRightPwm);

    Serial.println("}");
    return;
  }

  // --- LED mode ---
  if (cmd.startsWith("LEDS,")) {
    String mode = cmd.substring(5);
    mode.trim();
    mode.toUpperCase();

    if (mode == "AUTO") {
      ledMode = LED_MODE_AUTO;
      Serial.println("ACK,LEDS,AUTO");
      return;
    }

    if (mode == "GREEN") {
      if (activeError != 0) {
        Serial.println("ERR,LEDS,ERROR_ACTIVE");
        return;
      }

      ledMode = LED_MODE_GREEN;
      Serial.println("ACK,LEDS,GREEN");
      return;
    }

    Serial.println("ERR,LEDS,BAD_MODE");
    return;
  }

  // --- Error command ---
  // error1=true through error12=true
  // error0=false clears

  if (cmd.startsWith("error")) {
    int eq = cmd.indexOf('=');

    if (eq > 5) {
      int n = cmd.substring(5, eq).toInt();

      String v = cmd.substring(eq + 1);
      v.trim();

      if (v == "true" && n >= 1 && n <= 12) {
        activeError = n;
      } else {
        activeError = 0;
      }

      Serial.println("ACK");
    } else {
      Serial.println("ERR,BAD_ERROR_FORMAT");
    }

    return;
  }

  // --- JOY,<throttle>,<turn> ---
  // Input is -255 to 255 and is mapped to signed left/right PWM values.

  if (cmd.startsWith("JOY,")) {
    int c = cmd.indexOf(',', 4);

    if (c < 0) {
      Serial.println("ERR,BAD_JOY");
      return;
    }

    int throttle = constrain(cmd.substring(4, c).toInt(), -255, 255);
    int turn = constrain(cmd.substring(c + 1).toInt(), -255, 255);

    lastHbMs = millis();

    joyDrive(throttle, turn);

    Serial.print("ACK,JOY,");
    Serial.print(throttle);
    Serial.print(",");
    Serial.println(turn);

    return;
  }

  // --- STREAM,<interval_ms> ---
  if (cmd.startsWith("STREAM,")) {
    streamInterval = (unsigned long)cmd.substring(7).toInt();
    lastStreamMs = millis();

    Serial.print("ACK,STREAM,");
    Serial.println(streamInterval);

    return;
  }

  // --- RESET_ENC ---
  if (cmd == "RESET_ENC") {
    encL = 0;
    encR = 0;

    Serial.println("ACK,RESET_ENC");
    return;
  }

  // --- CAL ---
  // Drive exactly 12 inches, send stop.
  // ticksPerInch is auto-calculated and stored.

  if (cmd == "CAL") {
    encL = 0;
    encR = 0;

    calMode = true;
    streamInterval = 100;
    lastStreamMs = millis();

    Serial.println("CAL,READY,drive exactly 12 inches then send stop");
    return;
  }

  // --- Stop ---
  if (cmd.equalsIgnoreCase("stop")) {
    stopMotors();
    waitForMove = false;

    if (calMode) {
      calMode = false;
      streamInterval = 0;

      long totalTicks = (abs(encL) + abs(encR)) / 2;
      ticksPerInch = (int)round(totalTicks / 12.0f);

      prefs.begin("drive", false);
      prefs.putInt("tpi", ticksPerInch);
      prefs.end();

      Serial.print("CAL,DONE,raw_ticks=");
      Serial.print(totalTicks);
      Serial.print(",ticks_per_inch=");
      Serial.println(ticksPerInch);
    } else {
      Serial.println("ACK");
    }

    return;
  }

  // --- Move commands: <dir>,<dist_in>[,<speed>] ---
  // Examples:
  //   fwd,12
  //   fwd,-12   -> treated as rev,12
  //   fwd,12,180
  //   rev,12
  //   left,0
  //   right,0
  //
  // These require a prior HB.

  if (!waitForMove) {
    Serial.println("ERR,NO_HB");
    return;
  }

  int c1 = cmd.indexOf(',');

  if (c1 < 0) {
    Serial.println("ERR,BAD_FORMAT");
    return;
  }

  String dirStr = cmd.substring(0, c1);
  dirStr.toLowerCase();

  int c2 = cmd.indexOf(',', c1 + 1);
  float dist = 0.0f;
  int speed = 255;

  if (c2 < 0) {
    dist = cmd.substring(c1 + 1).toFloat();
  } else {
    dist = cmd.substring(c1 + 1, c2).toFloat();
    speed = constrain(cmd.substring(c2 + 1).toInt(), 0, 255);
  }

  DriveMode mode;

  if (dirStr == "fwd") {
    mode = MODE_FWD;
  } else if (dirStr == "rev") {
    mode = MODE_REV;
  } else if (dirStr == "left") {
    mode = MODE_LEFT;
  } else if (dirStr == "right") {
    mode = MODE_RIGHT;
  } else {
    Serial.print("ERR,UNKNOWN_DIR,");
    Serial.println(dirStr);
    return;
  }

  if (dist < 0.0f) {
    mode = oppositeMode(mode);
    dist = -dist;
  }

  drive(mode, dist, speed);
  waitForMove = false;

  Serial.print("ACK,");
  Serial.print(dirStr);
  Serial.print(",");
  Serial.print(dist, 1);
  Serial.print(",");
  Serial.println(speed);
}

// =========================
// Setup
// =========================
void setup() {
  Serial.begin(115200);
  delay(500);

  prefs.begin("drive", true);
  ticksPerInch = prefs.getInt("tpi", 100);
  prefs.end();

  pinMode(MOTOR_LEFT_FWD_PIN, OUTPUT);
  pinMode(MOTOR_LEFT_REV_PIN, OUTPUT);
  pinMode(MOTOR_RIGHT_FWD_PIN, OUTPUT);
  pinMode(MOTOR_RIGHT_REV_PIN, OUTPUT);
  pinMode(MOTOR_LEFT_PWM_PIN, OUTPUT);
  pinMode(MOTOR_RIGHT_PWM_PIN, OUTPUT);

  stopMotors();

  pinMode(ENC_L_A, INPUT_PULLUP);
  pinMode(ENC_L_B, INPUT_PULLUP);
  pinMode(ENC_R_A, INPUT_PULLUP);
  pinMode(ENC_R_B, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(ENC_L_A), isrLeftA, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_R_A), isrRightA, CHANGE);

  // NeoPixel moved to turret firmware; initialization removed

  lastHbMs = millis();

  Serial.println("OK,DRIVE_BOOT");
  Serial.printf(
    "INFO,TICKS_PER_INCH=%d,WATCHDOG_MS=%d\n",
    ticksPerInch,
    WATCHDOG_MS
  );
  Serial.printf(
    "INFO,MOTOR_PINS,LIN1=%d,LIN2=%d,LPWM=%d,RIN1=%d,RIN2=%d,RPWM=%d,STBY=EXT_5V\n",
    MOTOR_LEFT_FWD_PIN,
    MOTOR_LEFT_REV_PIN,
    MOTOR_LEFT_PWM_PIN,
    MOTOR_RIGHT_FWD_PIN,
    MOTOR_RIGHT_REV_PIN,
    MOTOR_RIGHT_PWM_PIN
  );
}

// =========================
// Loop
// =========================
void loop() {
  unsigned long now = millis();

  // Watchdog stop
  if (now - lastHbMs > WATCHDOG_MS && currentMode != MODE_STOP) {
    stopMotors();
    Serial.println("ERR,WATCHDOG_STOP");
  }

  // Distance target check
  if (useDistance && currentMode != MODE_STOP) {
    long avg = (abs(encL) + abs(encR)) / 2;

    if (avg >= targetTicks) {
      stopMotors();
      Serial.println("OK,DIST_REACHED");
    }
  }

  // Position stream
  if (streamInterval > 0 && now - lastStreamMs >= streamInterval) {
    lastStreamMs = now;

    Serial.print("POS,");
    Serial.print(encL);
    Serial.print(",");
    Serial.println(encR);
  }

  // NeoPixel handling moved to turret firmware

  while (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd.length() > 0) {
      parseCommand(cmd);
    }
  }
}
