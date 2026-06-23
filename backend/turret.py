from typing import Any, Dict

from serial_line import SerialLine


class TurretController:
    """
    Backend wrapper for turret_xiao.ino.

    turret_xiao.ino expects:
      PING -> OK,TURRET
      STATUS -> STATUS,<wifi_mode>,<ip>,<cam>,<accel>,<ultrasonic>
      Q -> T,<ax>,<ay>,<az>,<range_in>,<pitch_deg>,<roll_deg>,<flags>
      ACCEL_REINIT
      CAM_REINIT
      TILT_ZERO
    """

    def __init__(self, line: SerialLine):
        self.line = line
        self.last_status: Dict[str, Any] = {}
        self.last_telemetry: Dict[str, Any] = {}
        self.last_response = ""
        self.last_error = None
        self.camera_brightness = 0

    def _cmd(self, cmd: str, timeout: float = 0.7) -> str:
        try:
            resp = self.line.command(cmd, response_timeout=timeout)
            self.last_response = resp
            self.last_error = None
            return resp
        except Exception as e:
            self.last_error = str(e)
            raise

    def ping(self) -> str:
        return self._cmd("PING")

    def status(self) -> Dict[str, Any]:
        resp = self.line.command_matching(
            "STATUS",
            lambda line: line.startswith("STATUS,"),
            response_timeout=0.7,
        )
        self.last_response = resp
        parsed = self._parse_status(resp)
        if not parsed.get("parse_error"):
            self.last_status = parsed
            return parsed
        if self.last_status:
            return {
                **self.last_status,
                "raw": resp,
                "stale": True,
            }
        return parsed

    def telemetry(self) -> Dict[str, Any]:
        resp = self.line.command_matching(
            "Q",
            lambda line: line.startswith("T,"),
            response_timeout=0.7,
        )
        self.last_response = resp
        parsed = self._parse_telemetry(resp)
        if not parsed.get("parse_error"):
            self.last_telemetry = parsed
            return parsed
        if self.last_telemetry:
            return {
                **self.last_telemetry,
                "raw": resp,
                "stale": True,
            }
        return parsed

    def accel_reinit(self) -> str:
        return self._cmd("ACCEL_REINIT", timeout=1.0)

    def cam_reinit(self) -> str:
        return self._cmd("CAM_REINIT", timeout=2.0)

    def tilt_zero(self) -> str:
        return self._cmd("TILT_ZERO")

    def set_camera_brightness(self, level: int) -> str:
        level = max(-2, min(2, int(level)))
        resp = self.line.command_matching(
            f"CAM_BRIGHTNESS,{level}",
            lambda line: line.startswith("OK,CAM_BRIGHTNESS,") or line.startswith("ERR,CAM_BRIGHTNESS"),
            response_timeout=1.0,
        )
        self.last_response = resp
        self.last_error = None
        self.camera_brightness = level
        return resp

    @staticmethod
    def _parse_status(resp: str) -> Dict[str, Any]:
        # STATUS,STA,192.168.1.240,CAM_OK,ACCEL_ERR,US_ERR
        parts = resp.split(",")
        if len(parts) < 6 or parts[0] != "STATUS":
            return {"raw": resp, "parse_error": True}

        return {
            "raw": resp,
            "wifi_mode": parts[1],
            "ip": parts[2],
            "camera": parts[3],
            "accelerometer": parts[4],
            "ultrasonic": parts[5],
            "camera_ok": parts[3] == "CAM_OK",
            "accel_ok": not parts[4].endswith("ERR"),
            "ultrasonic_ok": parts[5] == "US_OK",
        }

    @staticmethod
    def _parse_telemetry(resp: str) -> Dict[str, Any]:
        # T,<ax>,<ay>,<az>,<range_in>,<pitch_deg>,<roll_deg>,<flags>
        parts = resp.split(",")
        if len(parts) < 8 or parts[0] != "T":
            return {"raw": resp, "parse_error": True}

        flags = parts[7]
        return {
            "raw": resp,
            "ax": float(parts[1]),
            "ay": float(parts[2]),
            "az": float(parts[3]),
            "range_in": float(parts[4]),
            "pitch_deg": float(parts[5]),
            "roll_deg": float(parts[6]),
            "flags": flags,
            "camera_ok": "C1" in flags,
            "accel_ok": "A1" in flags,
            "ultrasonic_ok": "U1" in flags,
            "wifi_ok": "W1" in flags,
        }
