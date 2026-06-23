from serial_line import SerialLine
from config import SERVO_BAUD, SERVO_PORT
from hobby_servo import HobbyServoController


class ServoController(HobbyServoController):
    def __init__(self):
        super().__init__(SerialLine(SERVO_PORT, SERVO_BAUD, name="servo"))
