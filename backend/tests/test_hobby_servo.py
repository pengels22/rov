import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hobby_servo import HobbyServoController


class FakeLine:
    port = "/dev/test"
    baud = 115200
    last_open_ok = True
    last_error = None

    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def command_matching(self, command, matcher, **_kwargs):
        self.commands.append(command)
        response = self.responses.pop(0)
        if not matcher(response):
            raise AssertionError(f"matcher rejected firmware response: {response}")
        return response


class HobbyServoProtocolTests(unittest.TestCase):
    def test_status_matches_firmware_state_and_battery(self):
        line = FakeLine([
            "STATE PAN_SPEED -30 PAN_HOMED YES HOME_SWITCH RELEASED TILT 45",
            "BATTERY 12.34 V",
        ])
        controller = HobbyServoController(line)
        status = controller.status()
        self.assertEqual(line.commands, ["?", "B"])
        self.assertEqual(status["pan"]["speed"], -30)
        self.assertTrue(status["pan"]["homed"])
        self.assertFalse(status["pan"]["home_switch_pressed"])
        self.assertEqual(status["tilt"]["angle"], 45)
        self.assertEqual(status["battery_v"], 12.34)

    def test_pan_command_is_signed_speed(self):
        line = FakeLine(["OK PAN_SPEED -100"])
        result = HobbyServoController(line).set_pan_speed(-150)
        self.assertEqual(line.commands, ["P-100"])
        self.assertEqual(result["speed"], -100)

    def test_combined_command_is_speed_and_tilt_angle(self):
        line = FakeLine(["OK PAN_SPEED 30 TILT 180"])
        result = HobbyServoController(line).set_pan_and_tilt(30, 220)
        self.assertEqual(line.commands, ["PT30,180"])
        self.assertEqual(result["pan_speed"], 30)
        self.assertEqual(result["tilt_angle"], 180)

    def test_pan_center_uses_firmware_stop(self):
        line = FakeLine(["OK PAN_STOP"])
        result = HobbyServoController(line).center(1)
        self.assertEqual(line.commands, ["STOP"])
        self.assertEqual(result["speed"], 0)

    def test_home_uses_firmware_home_command(self):
        line = FakeLine(["HOME OK PAN_ZERO"])
        result = HobbyServoController(line).home_pan()
        self.assertEqual(line.commands, ["H"])
        self.assertTrue(result["homed"])


if __name__ == "__main__":
    unittest.main()
