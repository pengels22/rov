import json
import time
from typing import Any, Dict

from serial_line import SerialLine


class DriveController:
    """
    Backend wrapper for drive_nano.ino.

    drive_nano.ino expects:
      HB -> ACK
      STATUS -> JSON
      LEDS,AUTO|GREEN -> ACK,LEDS,...
      JOY,<throttle>,<turn> -> ACK,JOY,...
      STREAM,<interval_ms> -> ACK,STREAM,...
      RESET_ENC -> ACK,RESET_ENC
      CAL -> CAL,READY,...
      stop -> ACK
      movement requires previous HB:
        fwd,<dist_in>,<speed>
        rev,<dist_in>,<speed>
        left,<dist_in>,<speed>
        right,<dist_in>,<speed>
    """

    def __init__(self, line: SerialLine):
        self.line = line
        self.last_status: Dict[str, Any] = {}
        self.last_response = ""
        self.last_error = None

    def heartbeat(self) -> str:
        return self._cmd("HB")

    def _cmd(self, cmd: str, timeout: float = 0.5) -> str:
        try:
            resp = self.line.command(cmd, response_timeout=timeout)
            self.last_response = resp
            self.last_error = None
            return resp
        except Exception as e:
            self.last_error = str(e)
            raise

    def status(self) -> Dict[str, Any]:
        resp = self._cmd("STATUS")
        try:
            self.last_status = json.loads(resp)
        except Exception:
            self.last_status = {"raw": resp, "parse_error": True}
        return self.last_status

    def stop(self) -> str:
        return self._cmd("stop")

    def joy(self, throttle: int, turn: int) -> str:
        throttle = max(-255, min(255, int(throttle)))
        turn = max(-255, min(255, int(turn)))
        return self._cmd(f"JOY,{throttle},{turn}")

    def _normalize_move(self, direction: str, dist_in: float) -> tuple[str, float]:
        direction = direction.lower()
        dist_in = float(dist_in)
        if dist_in >= 0:
            return direction, dist_in

        opposite = {
            "fwd": "rev",
            "rev": "fwd",
            "left": "right",
            "right": "left",
        }
        return opposite[direction], abs(dist_in)

    def move(self, direction: str, dist_in: float, speed: int) -> Dict[str, str]:
        direction = direction.lower()
        if direction not in ("fwd", "rev", "left", "right"):
            raise ValueError("direction must be fwd, rev, left, or right")

        speed = max(0, min(255, int(speed)))
        direction, dist_in = self._normalize_move(direction, dist_in)

        hb = self.heartbeat()
        time.sleep(0.03)
        ack = self._cmd(f"{direction},{dist_in:.2f},{speed}")
        return {"heartbeat": hb, "move": ack}

    def stream(self, interval_ms: int) -> str:
        interval_ms = max(0, int(interval_ms))
        return self._cmd(f"STREAM,{interval_ms}")

    def reset_encoders(self) -> str:
        return self._cmd("RESET_ENC")

    def set_error(self, error_num: int, enabled: bool = True) -> str:
        error_num = max(0, min(12, int(error_num)))
        val = "true" if enabled else "false"
        return self._cmd(f"error{error_num}={val}")

    def clear_error(self) -> str:
        return self._cmd("error0=false")

    def set_led_mode(self, mode: str) -> str:
        normalized = str(mode).strip().lower()
        if normalized not in ("auto", "green"):
            raise ValueError("mode must be auto or green")
        return self._cmd(f"LEDS,{normalized.upper()}")
