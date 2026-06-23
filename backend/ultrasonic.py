import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from config import (
    ULTRASONIC_ECHO_TIMEOUT_MS,
    ULTRASONIC_FRONT_ECHO_CHIP,
    ULTRASONIC_FRONT_ECHO_LINE,
    ULTRASONIC_FRONT_TRIGGER_CHIP,
    ULTRASONIC_FRONT_TRIGGER_LINE,
    ULTRASONIC_REAR_ECHO_CHIP,
    ULTRASONIC_REAR_ECHO_LINE,
    ULTRASONIC_REAR_TRIGGER_CHIP,
    ULTRASONIC_REAR_TRIGGER_LINE,
    ULTRASONIC_REFRESH_MS,
)


class PiUltrasonicService:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_snapshot: Dict[str, Any] = self._empty_snapshot("not sampled yet")
        self._last_sample_at = 0.0
        self._refresh_s = max(0.05, ULTRASONIC_REFRESH_MS / 1000.0)
        self._echo_timeout_s = max(0.01, ULTRASONIC_ECHO_TIMEOUT_MS / 1000.0)
        self._gpiomon = shutil.which("gpiomon")
        self._gpioset = shutil.which("gpioset")

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if now - self._last_sample_at < self._refresh_s:
                return dict(self._last_snapshot)

            self._last_snapshot = self._read_all()
            self._last_sample_at = now
            return dict(self._last_snapshot)

    def _empty_snapshot(self, status: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "available": False,
            "status": status,
            "front": self._empty_sensor("front", status),
            "rear": self._empty_sensor("rear", status),
        }

    def _empty_sensor(self, label: str, status: str) -> Dict[str, Any]:
        return {
            "name": label,
            "configured": False,
            "ok": False,
            "distance_in": None,
            "distance_mm": None,
            "status": status,
        }

    def _read_all(self) -> Dict[str, Any]:
        if not self._gpiomon or not self._gpioset:
            return self._empty_snapshot("gpiod tools unavailable")

        front = self._measure_sensor(
            "front",
            ULTRASONIC_FRONT_TRIGGER_CHIP,
            ULTRASONIC_FRONT_TRIGGER_LINE,
            ULTRASONIC_FRONT_ECHO_CHIP,
            ULTRASONIC_FRONT_ECHO_LINE,
        )
        rear = self._measure_sensor(
            "rear",
            ULTRASONIC_REAR_TRIGGER_CHIP,
            ULTRASONIC_REAR_TRIGGER_LINE,
            ULTRASONIC_REAR_ECHO_CHIP,
            ULTRASONIC_REAR_ECHO_LINE,
        )

        configured = front["configured"] or rear["configured"]
        ok = front["ok"] or rear["ok"]
        status = "ok" if ok else "configured" if configured else "not configured"
        return {
            "ok": ok,
            "available": configured,
            "status": status,
            "front": front,
            "rear": rear,
        }

    def _measure_sensor(
        self,
        label: str,
        trigger_chip: str,
        trigger_line: Optional[int],
        echo_chip: str,
        echo_line: Optional[int],
    ) -> Dict[str, Any]:
        if trigger_line is None or echo_line is None:
            return self._empty_sensor(label, "not configured")

        monitor = subprocess.Popen(
            [
                self._gpiomon,
                "-n",
                "2",
                "-F",
                "%e,%s,%n",
                echo_chip,
                str(echo_line),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            time.sleep(0.003)
            trigger = subprocess.run(
                [
                    self._gpioset,
                    "-m",
                    "time",
                    "-u",
                    "15",
                    trigger_chip,
                    f"{trigger_line}=1",
                ],
                capture_output=True,
                text=True,
                timeout=self._echo_timeout_s,
            )
            if trigger.returncode != 0:
                monitor.kill()
                monitor.wait(timeout=1.0)
                return {
                    **self._empty_sensor(label, "trigger failed"),
                    "configured": True,
                }

            stdout, _ = monitor.communicate(timeout=self._echo_timeout_s)
        except subprocess.TimeoutExpired:
            monitor.kill()
            try:
                monitor.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            return {
                **self._empty_sensor(label, "echo timeout"),
                "configured": True,
            }
        except Exception as exc:
            monitor.kill()
            try:
                monitor.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            return {
                **self._empty_sensor(label, str(exc)),
                "configured": True,
            }

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if len(lines) < 2:
            return {
                **self._empty_sensor(label, "incomplete echo"),
                "configured": True,
            }

        parsed = []
        for line in lines[:2]:
            event_type, sec, nsec = line.split(",", 2)
            parsed.append((int(event_type), int(sec), int(nsec)))

        rising = next((item for item in parsed if item[0] == 1), None)
        falling = next((item for item in parsed if item[0] == 0), None)
        if rising is None or falling is None:
            return {
                **self._empty_sensor(label, "missing pulse edge"),
                "configured": True,
            }

        start_s = rising[1] + (rising[2] / 1_000_000_000.0)
        end_s = falling[1] + (falling[2] / 1_000_000_000.0)
        pulse_s = end_s - start_s
        if pulse_s <= 0:
            return {
                **self._empty_sensor(label, "invalid pulse"),
                "configured": True,
            }

        distance_m = (pulse_s * 343.0) / 2.0
        distance_mm = round(distance_m * 1000.0, 1)
        distance_in = round(distance_mm / 25.4, 2)
        return {
            "name": label,
            "configured": True,
            "ok": True,
            "distance_in": distance_in,
            "distance_mm": distance_mm,
            "status": "ok",
        }
