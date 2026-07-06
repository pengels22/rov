import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drive import DriveController, DriveDeviceError, DriveSafetySupervisor


class FakeLine:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.commands = []
        self.lock = threading.Lock()

    def command_matching(self, command, matcher, **_kwargs):
        with self.lock:
            self.commands.append(command)
            response = self.responses.pop(0) if self.responses else "ACK"
        return response


class DriveControllerTests(unittest.TestCase):
    def test_device_error_is_raised(self):
        controller = DriveController(FakeLine(["ERR,BAD_JOY"]))
        with self.assertRaises(DriveDeviceError) as caught:
            controller.joy(1, 2)
        self.assertEqual(caught.exception.response, "ERR,BAD_JOY")

    def test_joystick_values_are_clamped(self):
        line = FakeLine()
        controller = DriveController(line)
        controller.joy(999, -999)
        self.assertEqual(line.commands, ["JOY,255,-255"])

    def test_distance_move_sends_heartbeat_first(self):
        line = FakeLine(["ACK", "ACK,fwd,12.0,120"])
        controller = DriveController(line)
        controller.move("fwd", 12, 120)
        self.assertEqual(line.commands, ["HB", "fwd,12.00,120"])


class DriveSafetySupervisorTests(unittest.TestCase):
    def test_failed_heartbeat_disables_motor(self):
        class FailingDrive:
            def heartbeat(self):
                raise OSError("serial gone")

            def stop(self):
                raise OSError("serial gone")

        disabled = threading.Event()
        supervisor = DriveSafetySupervisor(
            FailingDrive(),
            disabled.set,
            heartbeat_interval_s=0.02,
            client_timeout_s=0.05,
        )
        supervisor.start()
        self.assertTrue(disabled.wait(0.3))
        supervisor.stop()
        self.assertIn("serial gone", supervisor.last_heartbeat_error)

    def test_expired_client_lease_stops_and_disables(self):
        class WorkingDrive:
            def __init__(self):
                self.stop_called = threading.Event()

            def heartbeat(self):
                return "ACK"

            def stop(self):
                self.stop_called.set()

        drive = WorkingDrive()
        disabled = threading.Event()
        supervisor = DriveSafetySupervisor(
            drive,
            disabled.set,
            heartbeat_interval_s=0.02,
            client_timeout_s=0.05,
        )
        supervisor.renew_client_lease()
        supervisor.start()
        self.assertTrue(disabled.wait(0.4))
        supervisor.stop()
        self.assertTrue(drive.stop_called.is_set())
        self.assertEqual(supervisor.last_safety_reason, "client lease expired")


if __name__ == "__main__":
    unittest.main()
