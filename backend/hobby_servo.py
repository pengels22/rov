import time
from typing import Any, Dict

from config import PAN_SERVO_ID, SERVO_CENTER_POS, SERVO_MAX_POS, SERVO_MIN_POS, TILT_SERVO_ID
from serial_line import SerialLine


class HobbyServoController:
    def __init__(self, line: SerialLine):
        self.line = line
        self.last_command: Dict[str, Any] = {}
        self.last_status: Dict[str, Any] = {
            "mode": "serial_hobby_servo",
            "ready": False,
            "pan": {"servo_id": PAN_SERVO_ID, "position": SERVO_CENTER_POS},
            "tilt": {"servo_id": TILT_SERVO_ID, "position": SERVO_CENTER_POS},
        }
        self.last_error = None

    def _clamp_angle(self, angle: int) -> int:
        return max(SERVO_MIN_POS, min(SERVO_MAX_POS, int(angle)))

    def _parse_state(self, resp: str) -> Dict[str, Any]:
        parts = resp.split()
        if (
            len(parts) not in (5, 7)
            or parts[0] != "STATE"
            or parts[1] != "PAN"
            or parts[3] != "TILT"
            or (len(parts) == 7 and parts[5] != "BATT")
        ):
            return {"raw": resp, "parse_error": True}

        pan = int(parts[2])
        tilt = int(parts[4])
        status = {
            "mode": "serial_hobby_servo",
            "ready": True,
            "raw": resp,
            "last_command": self.last_command,
            "last_error": None,
            "safe_min_pos": SERVO_MIN_POS,
            "safe_max_pos": SERVO_MAX_POS,
            "center_pos": SERVO_CENTER_POS,
            "pan": {
                "servo_id": PAN_SERVO_ID,
                "position": pan,
                "angle": pan,
            },
            "tilt": {
                "servo_id": TILT_SERVO_ID,
                "position": tilt,
                "angle": tilt,
            },
            "servos": {
                "pan": {
                    "servo_id": PAN_SERVO_ID,
                    "position": pan,
                    "target_position": pan,
                    "angle": pan,
                },
                "tilt": {
                    "servo_id": TILT_SERVO_ID,
                    "position": tilt,
                    "target_position": tilt,
                    "angle": tilt,
                },
            },
        }
        if len(parts) == 7:
            status["battery_v"] = float(parts[6])
        return status

    @staticmethod
    def _parse_single_move(resp: str, expected_axis: str) -> Dict[str, int]:
        parts = resp.split()
        if len(parts) != 3 or parts[0] != "OK" or parts[1] != expected_axis:
            raise RuntimeError(f"unexpected servo response: {resp}")
        return {"position": int(parts[2]), "angle": int(parts[2])}

    @staticmethod
    def _parse_dual_move(resp: str) -> Dict[str, int]:
        parts = resp.split()
        if len(parts) != 5 or parts[0] != "OK" or parts[1] != "PAN" or parts[3] != "TILT":
            raise RuntimeError(f"unexpected servo response: {resp}")
        return {
            "pan_position": int(parts[2]),
            "pan_angle": int(parts[2]),
            "tilt_position": int(parts[4]),
            "tilt_angle": int(parts[4]),
        }

    def _cmd(self, cmd: str, matcher, timeout: float = 0.8) -> str:
        try:
            resp = self.line.command_matching(
                cmd,
                matcher,
                response_timeout=timeout,
                max_lines=12,
            )
            self.last_error = None
            return resp
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def _record_command(self, servo_id: int, position: int, time_ms: int, raw: str) -> Dict[str, Any]:
        result = {
            "servo_id": int(servo_id),
            "position": int(position),
            "angle": int(position),
            "time_ms": int(time_ms),
            "raw": raw,
            "ts": time.time(),
        }
        self.last_command = result
        return dict(result)

    def move(self, servo_id: int, position: int, time_ms: int = 1000) -> Dict[str, Any]:
        servo_id = int(servo_id)
        position = self._clamp_angle(position)
        time_ms = max(0, min(30_000, int(time_ms)))

        if servo_id == PAN_SERVO_ID:
            resp = self._cmd(
                f"P{position}",
                lambda line: line.startswith("OK PAN ") or line.startswith("ERR "),
            )
        elif servo_id == TILT_SERVO_ID:
            resp = self._cmd(
                f"T{position}",
                lambda line: line.startswith("OK TILT ") or line.startswith("ERR "),
            )
        else:
            raise ValueError(f"unknown servo id: {servo_id}")

        if resp.startswith("ERR "):
            self.last_error = resp
            raise RuntimeError(resp)

        parsed = self._parse_single_move(resp, "PAN" if servo_id == PAN_SERVO_ID else "TILT")
        return self._record_command(servo_id, parsed["position"], time_ms, resp)

    def move_both(self, pan_position: int, tilt_position: int, time_ms: int = 1000) -> Dict[str, Any]:
        pan_position = self._clamp_angle(pan_position)
        tilt_position = self._clamp_angle(tilt_position)
        time_ms = max(0, min(30_000, int(time_ms)))
        resp = self._cmd(
            f"PT{pan_position},{tilt_position}",
            lambda line: (line.startswith("OK PAN ") and " TILT " in line) or line.startswith("ERR "),
        )
        if resp.startswith("ERR "):
            self.last_error = resp
            raise RuntimeError(resp)

        parsed = self._parse_dual_move(resp)
        self.last_command = {
            "servo_id": None,
            "position": None,
            "angle": None,
            "time_ms": time_ms,
            "pan": parsed["pan_position"],
            "tilt": parsed["tilt_position"],
            "raw": resp,
            "ts": time.time(),
        }
        return dict(self.last_command)

    def center(self, servo_id: int, time_ms: int = 1000) -> Dict[str, Any]:
        return self.move(servo_id, SERVO_CENTER_POS, time_ms)

    def status(self) -> Dict[str, Any]:
        resp = self._cmd(
            "?",
            lambda line: line.startswith("STATE PAN ") or line.startswith("ERR "),
        )
        parsed = self._parse_state(resp)
        if parsed.get("parse_error"):
            if self.last_status:
                return {
                    **self.last_status,
                    "raw": resp,
                    "parse_error": True,
                    "stale": True,
                    "last_error": self.last_error,
                }
            return parsed
        parsed["last_command"] = self.last_command
        parsed["last_error"] = self.last_error
        self.last_status = parsed
        return parsed

    def device_status(self) -> Dict[str, Any]:
        return {
            "name": "servo",
            "mode": "serial_hobby_servo",
            "port": self.line.port,
            "baud": self.line.baud,
            "open_ok": self.line.last_open_ok,
            "last_error": self.last_error or self.line.last_error,
            "protocol": {
                "query": "?",
                "pan": "P<angle>",
                "tilt": "T<angle>",
                "both": "PT<pan>,<tilt>",
            },
        }
