import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rov_logs import RovLogs


class RovLogsTests(unittest.TestCase):
    def test_logs_keep_newest_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = RovLogs(tmp, max_lines=3)

            for i in range(5):
                logs.command_event("http_request", path=f"/api/{i}")

            records = logs.snapshot()["commands"]
            self.assertEqual(len(records), 3)
            self.assertEqual([record["path"] for record in records], [
                "/api/2",
                "/api/3",
                "/api/4",
            ])

    def test_heartbeat_is_not_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = RovLogs(tmp, max_lines=3)
            logs.command_event("tx", device="drive_nano", command="HB")
            logs.command_event("rx", device="drive_nano", command="HB", response="ACK")
            self.assertEqual(logs.snapshot()["commands"], [])


if __name__ == "__main__":
    unittest.main()
