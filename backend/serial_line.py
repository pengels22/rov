import threading
import time
from typing import Callable, Optional, List

import serial


class SerialLine:
    """
    Small locked line-oriented serial helper.

    Good for Nano and XIAO:
      - write ASCII command with trailing newline
      - read one line response
    Also usable for binary servo adapter writes using write_bytes().
    """

    def __init__(
        self,
        port: str,
        baud: int,
        timeout: float = 0.3,
        name: str = "serial",
        write_timeout: Optional[float] = None,
        use_rts_for_tx: bool = False,
        traffic_logger: Optional[Callable[..., None]] = None,
    ):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        # allow independent write timeout; default to same as read timeout
        self.write_timeout = write_timeout if write_timeout is not None else timeout
        self.name = name
        # Some half-duplex adapters require toggling RTS / DE for TX direction.
        self.use_rts_for_tx = bool(use_rts_for_tx)
        self.traffic_logger = traffic_logger
        self._lock = threading.RLock()
        self._ser: Optional[serial.Serial] = None
        self.last_error: Optional[str] = None
        self.last_open_ok = False
        self._last_logged_response_by_command: dict[str, str] = {}

    def _log(self, event: str, **fields) -> None:
        if not self.traffic_logger:
            return
        try:
            self.traffic_logger(
                event,
                device=self.name,
                port=self.port,
                **fields,
            )
        except Exception:
            pass

    def _log_exchange(self, cmd: str, response: str) -> None:
        clean_cmd = cmd.rstrip("\n")
        previous = self._last_logged_response_by_command.get(clean_cmd)
        if previous == response:
            return
        self._last_logged_response_by_command[clean_cmd] = response
        self._log("tx", command=clean_cmd)
        self._log("rx", command=clean_cmd, response=response)

    def open(self) -> serial.Serial:
        with self._lock:
            if self._ser and self._ser.is_open:
                return self._ser
            self._ser = serial.Serial(
                self.port,
                self.baud,
                timeout=self.timeout,
                write_timeout=self.write_timeout,
            )
            time.sleep(0.1)
            self.last_error = None
            self.last_open_ok = True
            return self._ser

    def close(self):
        with self._lock:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None

    def command(self, cmd: str, response_timeout: Optional[float] = None) -> str:
        """
        Send one line and read one line. Raises on serial errors.
        """
        with self._lock:
            ser = self.open()
            old_timeout = ser.timeout

            try:
                if response_timeout is not None:
                    ser.timeout = response_timeout
                ser.reset_input_buffer()
                ser.write((cmd.rstrip("\n") + "\n").encode("utf-8"))
                ser.flush()
                resp = ser.readline().decode("utf-8", errors="replace").strip()
                self._log_exchange(cmd, resp)
                self.last_error = None
                return resp
            except Exception as e:
                self.last_error = str(e)
                self._log("error", command=cmd.rstrip("\n"), error=str(e))
                self.close()
                raise
            finally:
                if self._ser and self._ser.is_open:
                    self._ser.timeout = old_timeout

    def command_matching(
        self,
        cmd: str,
        matcher: Callable[[str], bool],
        response_timeout: Optional[float] = None,
        max_lines: int = 8,
    ) -> str:
        """
        Send one line and read until a matching response arrives or timeout expires.
        Non-matching lines are treated as device noise/logging.
        """
        with self._lock:
            ser = self.open()
            old_timeout = ser.timeout
            timeout = response_timeout if response_timeout is not None else self.timeout
            deadline = time.monotonic() + timeout
            last_nonempty = ""

            try:
                ser.timeout = timeout
                ser.reset_input_buffer()
                ser.write((cmd.rstrip("\n") + "\n").encode("utf-8"))
                ser.flush()

                for _ in range(max_lines):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    ser.timeout = max(0.05, remaining)
                    resp = ser.readline().decode("utf-8", errors="replace").strip()
                    if not resp:
                        continue
                    last_nonempty = resp
                    if matcher(resp):
                        self._log_exchange(cmd, resp)
                        self.last_error = None
                        return resp

                self.last_error = None
                self._log_exchange(cmd, last_nonempty)
                return last_nonempty
            except Exception as e:
                self.last_error = str(e)
                self._log("error", command=cmd.rstrip("\n"), error=str(e))
                self.close()
                raise
            finally:
                if self._ser and self._ser.is_open:
                    self._ser.timeout = old_timeout

    def write_bytes(self, data: bytes):
        # Retry a few times on write timeout/OS errors to handle transient USB/IO stalls.
        attempts = 3
        delay = 0.02
        with self._lock:
            for attempt in range(1, attempts + 1):
                ser = self.open()
                try:
                        # If adapter needs RTS asserted for TX, toggle it here.
                        if self.use_rts_for_tx:
                            try:
                                ser.setRTS(True)
                            except Exception:
                                try:
                                    ser.rts = True
                                except Exception:
                                    pass

                        ser.write(data)
                        ser.flush()

                        if self.use_rts_for_tx:
                            try:
                                ser.setRTS(False)
                            except Exception:
                                try:
                                    ser.rts = False
                                except Exception:
                                    pass

                        self.last_error = None
                        return
                except serial.SerialTimeoutException as e:
                    self.last_error = f"write timeout: {e}"
                    # close and retry
                    self.close()
                except OSError as e:
                    self.last_error = f"os error on write: {e}"
                    self.close()
                except Exception as e:
                    self.last_error = str(e)
                    self.close()
                    raise

                if attempt < attempts:
                    time.sleep(delay)

            # If we exhausted attempts, raise a SerialTimeoutException for caller
            raise serial.SerialTimeoutException(f"write timed out after {attempts} attempts to {self.port}")

    def read_available_lines(self, max_lines: int = 20) -> List[str]:
        """
        Non-blocking-ish drain of pending line data.
        """
        lines = []
        with self._lock:
            ser = self.open()
            for _ in range(max_lines):
                if not ser.in_waiting:
                    break
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    lines.append(line)
        return lines

    def status(self):
        return {
            "name": self.name,
            "port": self.port,
            "baud": self.baud,
            "open_ok": self.last_open_ok,
            "last_error": self.last_error,
        }
