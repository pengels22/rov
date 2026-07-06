from serial_line import SerialLine
from config import SERVO_BAUD, TURRET_SERVO_PORT
from hobby_servo import HobbyServoController


class ServoController(HobbyServoController):
    def __init__(self, traffic_logger=None):
        super().__init__(SerialLine(
            TURRET_SERVO_PORT,
            SERVO_BAUD,
            name="turret_servos",
            traffic_logger=traffic_logger,
        ))
