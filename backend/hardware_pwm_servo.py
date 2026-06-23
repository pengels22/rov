import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from config import (
    PAN_SERVO_ID,
    PAN_SERVO_PWM_CHANNEL,
    PAN_SERVO_PWM_DEVICE,
    SERVO_CENTER_POS,
    SERVO_HOLD_ACTIVE_WHEN_IDLE,
    SERVO_IDLE_RELEASE_MS,
    SERVO_MAX_POS,
    SERVO_MIN_POS,
    SERVO_PWM_MAX_US,
    SERVO_PWM_MIN_US,
    SERVO_PWM_PERIOD_US,
    TILT_SERVO_ID,
    TILT_SERVO_PWM_CHANNEL,
    TILT_SERVO_PWM_DEVICE,
)


def _write_text(path: Path, value: str):
    path.write_text(value, encoding="ascii")


@dataclass
class _ServoSpec:
    servo_id: int
    label: str
    header_pin: int
    pwm_device: str
    pwm_channel: int
    position: int = SERVO_CENTER_POS
    target_position: int = SERVO_CENTER_POS
    signal_until: float = 0.0
    duty_ns: int = 1_500_000
    pwmchip_path: Optional[Path] = None
    channel_path: Optional[Path] = None
    enabled: bool = False


class _ServoPWMStatus:
    def __init__(self, controller: "HardwarePWMServoController"):
        self._controller = controller

    def status(self) -> Dict[str, Any]:
        return self._controller.device_status()


class HardwarePWMServoController:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ready = False
        self._hold_active_when_idle = bool(SERVO_HOLD_ACTIVE_WHEN_IDLE)
        self._idle_release_s = max(0.0, SERVO_IDLE_RELEASE_MS / 1000.0)
        self.last_command: Dict[str, Any] = {}
        self.last_error: Optional[str] = None
        self.line = _ServoPWMStatus(self)
        self._servos = {
            PAN_SERVO_ID: _ServoSpec(
                servo_id=PAN_SERVO_ID,
                label="pan",
                header_pin=21,
                pwm_device=PAN_SERVO_PWM_DEVICE,
                pwm_channel=PAN_SERVO_PWM_CHANNEL,
            ),
            TILT_SERVO_ID: _ServoSpec(
                servo_id=TILT_SERVO_ID,
                label="tilt",
                header_pin=23,
                pwm_device=TILT_SERVO_PWM_DEVICE,
                pwm_channel=TILT_SERVO_PWM_CHANNEL,
            ),
        }
        self._ensure_ready()
        self._thread = threading.Thread(target=self._watchdog_loop, name="hardware-pwm-servo", daemon=True)
        self._thread.start()

    def _find_pwmchip(self, device_path: str) -> Path:
        target = Path(device_path).resolve()
        for pwmchip in Path("/sys/class/pwm").glob("pwmchip*"):
            device = (pwmchip / "device").resolve()
            if device == target:
                return pwmchip
        raise FileNotFoundError(f"no pwmchip found for {device_path}")

    def _export_channel(self, pwmchip: Path, channel: int) -> Path:
        channel_path = pwmchip / f"pwm{channel}"
        if channel_path.exists():
            return channel_path
        _write_text(pwmchip / "export", str(channel))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if channel_path.exists():
                return channel_path
            time.sleep(0.02)
        raise TimeoutError(f"timed out exporting {pwmchip.name} channel {channel}")

    def _enable_channel(self, spec: _ServoSpec):
        if not spec.channel_path:
            raise RuntimeError(f"{spec.label} PWM channel not ready")
        _write_text(spec.channel_path / "enable", "1")
        spec.enabled = True

    def _disable_channel(self, spec: _ServoSpec):
        if spec.channel_path and spec.enabled:
            _write_text(spec.channel_path / "enable", "0")
            spec.enabled = False

    def _position_to_pulse_us(self, position: int) -> int:
        position = max(SERVO_MIN_POS, min(SERVO_MAX_POS, int(position)))
        span = SERVO_MAX_POS - SERVO_MIN_POS
        if span <= 0:
            return SERVO_PWM_MIN_US
        fraction = (position - SERVO_MIN_POS) / float(span)
        pulse = SERVO_PWM_MIN_US + ((SERVO_PWM_MAX_US - SERVO_PWM_MIN_US) * fraction)
        return int(round(pulse))

    def _set_position(self, spec: _ServoSpec, position: int):
        if not spec.channel_path:
            raise RuntimeError(f"{spec.label} PWM channel not ready")
        duty_ns = self._position_to_pulse_us(position) * 1000
        period_ns = SERVO_PWM_PERIOD_US * 1000

        if spec.enabled:
            _write_text(spec.channel_path / "enable", "0")
            spec.enabled = False

        _write_text(spec.channel_path / "period", str(period_ns))
        _write_text(spec.channel_path / "duty_cycle", str(duty_ns))
        _write_text(spec.channel_path / "enable", "1")
        spec.enabled = True
        spec.position = position
        spec.target_position = position
        spec.duty_ns = duty_ns

    def _ensure_ready(self):
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                for spec in self._servos.values():
                    spec.pwmchip_path = self._find_pwmchip(spec.pwm_device)
                    spec.channel_path = self._export_channel(spec.pwmchip_path, spec.pwm_channel)
                    self._set_position(spec, SERVO_CENTER_POS)
                    if not self._hold_active_when_idle:
                        self._disable_channel(spec)
                self._ready = True
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                self._ready = False
                raise

    def _watchdog_loop(self):
        while not self._stop.is_set():
            try:
                self._ensure_ready()
                if self._hold_active_when_idle:
                    time.sleep(0.05)
                    continue
                now = time.monotonic()
                with self._lock:
                    for spec in self._servos.values():
                        if spec.enabled and now >= spec.signal_until:
                            self._disable_channel(spec)
                time.sleep(0.02)
            except Exception as exc:
                self.last_error = str(exc)
                time.sleep(0.2)

    def move(self, servo_id: int, position: int, time_ms: int = 1000) -> Dict[str, Any]:
        servo_id = int(servo_id)
        if servo_id not in self._servos:
            raise ValueError(f"unknown servo id: {servo_id}")
        self._ensure_ready()

        position = max(SERVO_MIN_POS, min(SERVO_MAX_POS, int(position)))
        time_ms = max(0, min(30_000, int(time_ms)))
        now = time.monotonic()
        with self._lock:
            spec = self._servos[servo_id]
            self._set_position(spec, position)
            spec.signal_until = now + (time_ms / 1000.0) + self._idle_release_s
            self.last_command = {
                "servo_id": servo_id,
                "position": position,
                "time_ms": time_ms,
                "pulse_us": spec.duty_ns // 1000,
                "pwm_device": spec.pwm_device,
                "pwmchip": spec.pwmchip_path.name if spec.pwmchip_path else None,
                "channel": spec.pwm_channel,
                "ts": time.time(),
            }
            self.last_error = None
            return dict(self.last_command)

    def center(self, servo_id: int, time_ms: int = 1000) -> Dict[str, Any]:
        return self.move(servo_id, SERVO_CENTER_POS, time_ms)

    def status(self) -> Dict[str, Any]:
        try:
            self._ensure_ready()
        except Exception:
            pass
        with self._lock:
            servos = {
                spec.label: {
                    "servo_id": spec.servo_id,
                    "position": spec.position,
                    "target_position": spec.target_position,
                    "header_pin": spec.header_pin,
                    "pwm_device": spec.pwm_device,
                    "pwmchip": spec.pwmchip_path.name if spec.pwmchip_path else None,
                    "channel": spec.pwm_channel,
                    "enabled": spec.enabled,
                }
                for spec in self._servos.values()
            }
        return {
            "mode": "hardware_pwm",
            "ready": self._ready,
            "last_command": self.last_command,
            "last_error": self.last_error,
            "safe_min_pos": SERVO_MIN_POS,
            "safe_max_pos": SERVO_MAX_POS,
            "center_pos": SERVO_CENTER_POS,
            "pulse_min_us": SERVO_PWM_MIN_US,
            "pulse_max_us": SERVO_PWM_MAX_US,
            "period_us": SERVO_PWM_PERIOD_US,
            "hold_active_when_idle": self._hold_active_when_idle,
            "idle_release_ms": SERVO_IDLE_RELEASE_MS,
            "servos": servos,
        }

    def device_status(self) -> Dict[str, Any]:
        try:
            self._ensure_ready()
        except Exception:
            pass
        return {
            "name": "servo",
            "mode": "hardware_pwm",
            "ready": self._ready,
            "last_error": self.last_error,
            "pins": {
                "pan": {
                    "header_pin": 21,
                    "pwm_device": PAN_SERVO_PWM_DEVICE,
                    "channel": PAN_SERVO_PWM_CHANNEL,
                },
                "tilt": {
                    "header_pin": 23,
                    "pwm_device": TILT_SERVO_PWM_DEVICE,
                    "channel": TILT_SERVO_PWM_CHANNEL,
                },
            },
        }

    def close(self):
        self._stop.set()
        if hasattr(self, "_thread") and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self._lock:
            for spec in self._servos.values():
                try:
                    self._disable_channel(spec)
                except Exception:
                    pass
