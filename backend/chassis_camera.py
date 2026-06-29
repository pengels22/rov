import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from config import CHASSIS_CAMERA_COMMANDS, CHASSIS_CAMERA_ENABLED


class ChassisCameraService:
    def __init__(self):
        self.enabled = bool(CHASSIS_CAMERA_ENABLED)
        self._commands = [list(cmd) for cmd in CHASSIS_CAMERA_COMMANDS]
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._latest_frame: Optional[bytes] = None
        self._frame_id = 0
        self._last_frame_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._active_command: Optional[List[str]] = None
        self._running = False

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="chassis-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._stop_process()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "ok": self.enabled and self._latest_frame is not None and self._last_error is None,
            "stream_url": "/api/chassis/camera/stream" if self.enabled else None,
            "active_command": " ".join(self._active_command) if self._active_command else None,
            "frame_id": self._frame_id,
            "last_frame_at": self._last_frame_at,
            "last_error": self._last_error,
        }

    def wait_for_frame(
        self,
        last_frame_id: int = 0,
        timeout_s: float = 5.0,
    ) -> tuple[Optional[bytes], int]:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while (
                self.enabled
                and self._frame_id <= last_frame_id
                and time.monotonic() < deadline
                and not self._stop_event.is_set()
            ):
                self._condition.wait(timeout=0.25)

            return self._latest_frame, self._frame_id

    def _select_command(self) -> Optional[List[str]]:
        for command in self._commands:
            if command and shutil.which(command[0]):
                return command
        return None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            command = self._select_command()
            if not command:
                self._last_error = (
                    "no chassis camera command found: "
                    "rpicam-vid, libcamera-vid, or gst-launch-1.0"
                )
                self._running = False
                time.sleep(2.0)
                continue

            self._active_command = command
            self._running = True
            self._last_error = None
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                self._read_mjpeg_stdout(self._process)
            except Exception as exc:
                self._last_error = str(exc)
            finally:
                self._running = False
                self._stop_process()

            if not self._stop_event.is_set():
                time.sleep(1.0)

    def _read_mjpeg_stdout(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
            raise RuntimeError("camera process stdout is not available")

        buffer = bytearray()
        while not self._stop_event.is_set():
            chunk = process.stdout.read(4096)
            if not chunk:
                if process.poll() is not None:
                    self._last_error = f"camera process exited with {process.returncode}"
                    return
                time.sleep(0.01)
                continue

            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                if start < 0:
                    if len(buffer) > 1_000_000:
                        del buffer[:-2]
                    break

                end = buffer.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    break

                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                self._publish_frame(frame)

    def _publish_frame(self, frame: bytes) -> None:
        with self._condition:
            self._latest_frame = frame
            self._frame_id += 1
            self._last_frame_at = time.time()
            self._last_error = None
            self._condition.notify_all()

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if not process:
            return

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
