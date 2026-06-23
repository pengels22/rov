#include <Servo.h>

// Servo pins
const int PAN_PIN  = 2;
const int TILT_PIN = 3;

// Safe limits for normal hobby servos
const int PAN_MIN = 0;
const int PAN_MAX = 180;
const int TILT_MIN = 0;
const int TILT_MAX = 180;

Servo panServo;
Servo tiltServo;

int panAngle = 90;
int tiltAngle = 90;

String inputLine = "";

void setup() {
  Serial.begin(115200);

  // Pro Micro / Leonardo USB serial can take a moment to appear.
  delay(1500);

  panServo.attach(PAN_PIN);
  tiltServo.attach(TILT_PIN);

  panServo.write(panAngle);
  tiltServo.write(tiltAngle);

  delay(500);

  Serial.println("READY TWO_SERVO_PROMICRO");
  Serial.println("CMDS: P90 T45 PT90,45 ?");
}

void loop() {
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
    return;
  }

  // Pan only: P90
  if (cmd.startsWith("P") && !cmd.startsWith("PT")) {
    String value = cmd.substring(1);
    value.trim();

    if (!isValidAngleString(value, PAN_MIN, PAN_MAX)) {
      Serial.println("ERR BAD_PAN");
      return;
    }

    setPan(value.toInt());

    Serial.print("OK PAN ");
    Serial.println(panAngle);
    return;
  }

  // Tilt only: T45
  if (cmd.startsWith("T")) {
    String value = cmd.substring(1);
    value.trim();

    if (!isValidAngleString(value, TILT_MIN, TILT_MAX)) {
      Serial.println("ERR BAD_TILT");
      return;
    }

    setTilt(value.toInt());

    Serial.print("OK TILT ");
    Serial.println(tiltAngle);
    return;
  }

  // Pan and tilt together: PT90,45
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

    if (!isValidAngleString(panValue, PAN_MIN, PAN_MAX)) {
      Serial.println("ERR BAD_PAN");
      return;
    }

    if (!isValidAngleString(tiltValue, TILT_MIN, TILT_MAX)) {
      Serial.println("ERR BAD_TILT");
      return;
    }

    setPan(panValue.toInt());
    setTilt(tiltValue.toInt());

    Serial.print("OK PAN ");
    Serial.print(panAngle);
    Serial.print(" TILT ");
    Serial.println(tiltAngle);
    return;
  }

  Serial.println("ERR BAD_CMD");
}

void setPan(int angle) {
  panAngle = constrain(angle, PAN_MIN, PAN_MAX);
  panServo.write(panAngle);
}

void setTilt(int angle) {
  tiltAngle = constrain(angle, TILT_MIN, TILT_MAX);
  tiltServo.write(tiltAngle);
}

void reportState() {
  Serial.print("STATE PAN ");
  Serial.print(panAngle);
  Serial.print(" TILT ");
  Serial.println(tiltAngle);
}

bool isValidAngleString(String value, int minAngle, int maxAngle) {
  value.trim();

  if (value.length() == 0) {
    return false;
  }

  for (int i = 0; i < value.length(); i++) {
    if (!isDigit(value.charAt(i))) {
      return false;
    }
  }

  int angle = value.toInt();

  if (angle < minAngle || angle > maxAngle) {
    return false;
  }

  return true;
}