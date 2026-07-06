import time
from typing import Any, Dict

from config import (
    PAN_MAX_SPEED,
    PAN_MIN_SPEED,
    PAN_SERVO_ID,
    SERVO_CENTER_POS,
    TILT_MAX_ANGLE,
    TILT_MIN_ANGLE,
    TILT_SERVO_ID,
)
from serial_line import SerialLine


class HobbyServoController:
    """Adapter for the exact protocol implemented by turret_servos.ino."""

    def __init__(self, line: SerialLine):
        self.line = line
        self.last_command: Dict[str, Any] = {}
        self.last_status: Dict[str, Any] = {
            "mode": "pan_motor_tilt_servo",
            "ready": False,
            "pan": {"servo_id": PAN_SERVO_ID, "speed": 0},
            "tilt": {"servo_id": TILT_SERVO_ID, "position": SERVO_CENTER_POS, "angle": SERVO_CENTER_POS},
        }
        self.last_error = None

    @staticmethod
    def _clamp_pan_speed(speed: int) -> int:
        return max(PAN_MIN_SPEED, min(PAN_MAX_SPEED, int(speed)))

    @staticmethod
    def _clamp_tilt_angle(angle: int) -> int:
        return max(TILT_MIN_ANGLE, min(TILT_MAX_ANGLE, int(angle)))

    def _cmd(self, cmd: str, matcher, timeout: float = 0.8) -> str:
        try:
            resp = self.line.command_matching(
                cmd, matcher, response_timeout=timeout, max_lines=20
            )
            if not resp:
                raise TimeoutError(f"servo controller did not respond to {cmd}")
            if resp.startswith("ERR "):
                raise RuntimeError(resp)
            self.last_error = None
            return resp
        except Exception as exc:
            self.last_error = str(exc)
            raise

    @staticmethod
    def _parse_state(resp: str) -> Dict[str, Any]:
        # STATE PAN_SPEED <n> PAN_HOMED YES|NO HOME_SWITCH PRESSED|RELEASED TILT <n>
        parts = resp.split()
        if (
            len(parts) != 9
            or parts[0:2] != ["STATE", "PAN_SPEED"]
            or parts[3] != "PAN_HOMED"
            or parts[5] != "HOME_SWITCH"
            or parts[7] != "TILT"
        ):
            return {"raw": resp, "parse_error": True}
        pan_speed = int(parts[2])
        tilt_angle = int(parts[8])
        return {
            "mode": "pan_motor_tilt_servo",
            "ready": True,
            "raw": resp,
            "pan": {
                "servo_id": PAN_SERVO_ID,
                "speed": pan_speed,
                "homed": parts[4] == "YES",
                "home_switch_pressed": parts[6] == "PRESSED",
            },
            "tilt": {
                "servo_id": TILT_SERVO_ID,
                "position": tilt_angle,
                "angle": tilt_angle,
            },
        }

    @staticmethod
    def _parse_battery(resp: str) -> float:
        parts = resp.split()
        if len(parts) != 3 or parts[0] != "BATTERY" or parts[2] != "V":
            raise RuntimeError(f"unexpected battery response: {resp}")
        return float(parts[1])

    def set_pan_speed(self, speed: int) -> Dict[str, Any]:
        speed = self._clamp_pan_speed(speed)
        resp = self._cmd(
            f"P{speed}",
            lambda line: line.startswith("OK PAN_SPEED ") or line.startswith("ERR "),
        )
        actual = int(resp.split()[2])
        result = {
            "servo_id": PAN_SERVO_ID,
            "speed": actual,
            "raw": resp,
            "ts": time.time(),
        }
        self.last_command = result
        return dict(result)

    def set_tilt_angle(self, angle: int) -> Dict[str, Any]:
        angle = self._clamp_tilt_angle(angle)
        resp = self._cmd(
            f"T{angle}",
            lambda line: line.startswith("OK TILT ") or line.startswith("ERR "),
        )
        actual = int(resp.split()[2])
        result = {
            "servo_id": TILT_SERVO_ID,
            "position": actual,
            "angle": actual,
            "raw": resp,
            "ts": time.time(),
        }
        self.last_command = result
        return dict(result)

    def set_pan_and_tilt(self, pan_speed: int, tilt_angle: int) -> Dict[str, Any]:
        pan_speed = self._clamp_pan_speed(pan_speed)
        tilt_angle = self._clamp_tilt_angle(tilt_angle)
        resp = self._cmd(
            f"PT{pan_speed},{tilt_angle}",
            lambda line: (
                line.startswith("OK PAN_SPEED ") and " TILT " in line
            ) or line.startswith("ERR "),
        )
        parts = resp.split()
        if len(parts) != 5 or parts[0:2] != ["OK", "PAN_SPEED"] or parts[3] != "TILT":
            raise RuntimeError(f"unexpected servo response: {resp}")
        result = {
            "pan_speed": int(parts[2]),
            "tilt_position": int(parts[4]),
            "tilt_angle": int(parts[4]),
            "raw": resp,
            "ts": time.time(),
        }
        self.last_command = result
        return dict(result)

    # Compatibility entry points used by existing API routes.
    def move(self, servo_id: int, value: int, time_ms: int = 0) -> Dict[str, Any]:
        if int(servo_id) == PAN_SERVO_ID:
            return self.set_pan_speed(value)
        if int(servo_id) == TILT_SERVO_ID:
            return self.set_tilt_angle(value)
        raise ValueError(f"unknown servo id: {servo_id}")

    def move_both(self, pan_speed: int, tilt_angle: int, time_ms: int = 0) -> Dict[str, Any]:
        return self.set_pan_and_tilt(pan_speed, tilt_angle)

    def center(self, servo_id: int, time_ms: int = 0) -> Dict[str, Any]:
        if int(servo_id) == PAN_SERVO_ID:
            return self.stop_pan()
        return self.move(servo_id, SERVO_CENTER_POS, time_ms)

    def stop_pan(self) -> Dict[str, Any]:
        resp = self._cmd(
            "STOP",
            lambda line: line == "OK PAN_STOP" or line.startswith("ERR "),
        )
        result = {
            "servo_id": PAN_SERVO_ID,
            "speed": 0,
            "raw": resp,
            "ts": time.time(),
        }
        self.last_command = result
        return dict(result)

    def home_pan(self) -> Dict[str, Any]:
        resp = self._cmd(
            "H",
            lambda line: line.startswith("HOME OK ") or line.startswith("ERR "),
            timeout=45.0,
        )
        result = {"homed": True, "speed": 0, "raw": resp, "ts": time.time()}
        self.last_command = result
        return dict(result)

    def status(self) -> Dict[str, Any]:
        resp = self._cmd(
            "?",
            lambda line: line.startswith("STATE PAN_SPEED ") or line.startswith("ERR "),
        )
        parsed = self._parse_state(resp)
        if parsed.get("parse_error"):
            raise RuntimeError(f"unexpected servo state: {resp}")
        battery_resp = self._cmd(
            "B",
            lambda line: line.startswith("BATTERY ") or line.startswith("ERR "),
        )
        parsed["battery_v"] = self._parse_battery(battery_resp)
        parsed["last_command"] = self.last_command
        parsed["last_error"] = None
        self.last_status = parsed
        return parsed

    def device_status(self) -> Dict[str, Any]:
        return {
            "name": "servo",
            "mode": "pan_motor_tilt_servo",
            "port": self.line.port,
            "baud": self.line.baud,
            "open_ok": self.line.last_open_ok,
            "last_error": self.last_error or self.line.last_error,
            "protocol": {
                "query": "?",
                "pan_speed": "P<-100..100>",
                "tilt_angle": "T<0..180>",
                "both": "PT<pan_speed>,<tilt_angle>",
                "home": "H",
                "stop_pan": "STOP",
                "battery": "B",
            },
        }
