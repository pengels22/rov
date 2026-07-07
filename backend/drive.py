import json
import threading
import time
from typing import Any, Callable, Dict, Optional

from serial_line import SerialLine


class DriveDeviceError(RuntimeError):
    def __init__(self, response: str):
        super().__init__(f"drive controller rejected command: {response}")
        self.response = response


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
            resp = self.line.command_matching(
                cmd,
                lambda line: (
                    line == "ACK"
                    or line.startswith("ACK,")
                    or line.startswith("ERR")
                ),
                response_timeout=timeout,
                max_lines=12,
            )
            self.last_response = resp
            if not resp:
                raise TimeoutError(f"drive controller did not respond to {cmd}")
            if resp.upper().startswith("ERR"):
                raise DriveDeviceError(resp)
            self.last_error = None
            return resp
        except Exception as e:
            self.last_error = str(e)
            raise

    def status(self) -> Dict[str, Any]:
        try:
            resp = self.line.command_matching(
                "STATUS",
                lambda line: line.startswith("{") or line.startswith("ERR"),
                response_timeout=0.5,
                max_lines=12,
            )
            if not resp:
                raise TimeoutError("drive controller did not respond to STATUS")
            if resp.upper().startswith("ERR"):
                raise DriveDeviceError(resp)
        except Exception as exc:
            self.last_error = str(exc)
            raise
        try:
            self.last_status = json.loads(resp)
            self.last_error = None
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


class DriveSafetySupervisor:
    """Independent drive heartbeat safety supervisor."""

    def __init__(
        self,
        drive: DriveController,
        disable_motor: Callable[[], None],
        heartbeat_interval_s: float = 1.0,
        system_logger: Optional[Callable[..., None]] = None,
    ):
        self.drive = drive
        self.disable_motor = disable_motor
        self.system_logger = system_logger
        self.heartbeat_interval_s = max(0.1, float(heartbeat_interval_s))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_heartbeat_at: Optional[float] = None
        self.last_heartbeat_error: Optional[str] = None
        self.last_safety_reason: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="drive-safety-supervisor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=self.heartbeat_interval_s + 1.0)

    def renew_client_lease(self) -> None:
        return None

    def disconnect_client(self) -> None:
        return None

    def safe_shutdown(self, reason: str) -> None:
        self.last_safety_reason = reason
        if self.system_logger:
            try:
                self.system_logger("warning", "drive safety shutdown", reason=reason)
            except Exception:
                pass
        try:
            self.drive.stop()
        except Exception:
            pass
        try:
            self.disable_motor()
        except Exception:
            pass

    def snapshot(self) -> Dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_heartbeat_error": self.last_heartbeat_error,
            "client_lease_enabled": False,
            "last_safety_reason": self.last_safety_reason,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                self.drive.heartbeat()
                self.last_heartbeat_at = time.time()
                self.last_heartbeat_error = None
            except Exception as exc:
                self.last_heartbeat_error = str(exc)
                self.safe_shutdown("drive heartbeat failed")

            elapsed = time.monotonic() - cycle_started
            self._stop_event.wait(max(0.0, self.heartbeat_interval_s - elapsed))
