import ctypes
import ctypes.util
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import (
    PAN_SERVO_GPIO_CHIP,
    PAN_SERVO_GPIO_LINE,
    PAN_SERVO_ID,
    SERVO_CENTER_POS,
    SERVO_HOLD_ACTIVE_WHEN_IDLE,
    SERVO_IDLE_RELEASE_MS,
    SERVO_MAX_POS,
    SERVO_MIN_POS,
    SERVO_PWM_MAX_US,
    SERVO_PWM_MIN_US,
    SERVO_PWM_PERIOD_US,
    TILT_SERVO_GPIO_CHIP,
    TILT_SERVO_GPIO_LINE,
    TILT_SERVO_ID,
)


class _LibGpiod:
    def __init__(self):
        lib_path = ctypes.util.find_library("gpiod")
        if not lib_path:
            raise RuntimeError("libgpiod not found")

        self.lib = ctypes.CDLL(lib_path)
        self.lib.gpiod_chip_open_by_name.argtypes = [ctypes.c_char_p]
        self.lib.gpiod_chip_open_by_name.restype = ctypes.c_void_p
        self.lib.gpiod_chip_close.argtypes = [ctypes.c_void_p]
        self.lib.gpiod_chip_close.restype = None
        self.lib.gpiod_chip_get_line.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.lib.gpiod_chip_get_line.restype = ctypes.c_void_p
        self.lib.gpiod_line_request_output.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.lib.gpiod_line_request_output.restype = ctypes.c_int
        self.lib.gpiod_line_set_value.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.gpiod_line_set_value.restype = ctypes.c_int
        self.lib.gpiod_line_release.argtypes = [ctypes.c_void_p]
        self.lib.gpiod_line_release.restype = None

    def open_chip(self, chip_name: str):
        handle = self.lib.gpiod_chip_open_by_name(chip_name.encode("utf-8"))
        if not handle:
            raise OSError(f"failed to open {chip_name}")
        return handle

    def get_line(self, chip_handle, line_offset: int):
        handle = self.lib.gpiod_chip_get_line(chip_handle, int(line_offset))
        if not handle:
            raise OSError(f"failed to get GPIO line {line_offset}")
        return handle

    def request_output(self, line_handle, consumer: str, initial_value: int = 0):
        rc = self.lib.gpiod_line_request_output(
            line_handle,
            consumer.encode("utf-8"),
            int(initial_value),
        )
        if rc != 0:
            raise OSError(f"failed to request GPIO output for {consumer}")

    def set_value(self, line_handle, value: int):
        rc = self.lib.gpiod_line_set_value(line_handle, int(value))
        if rc != 0:
            raise OSError("failed to set GPIO value")

    def release_line(self, line_handle):
        if line_handle:
            self.lib.gpiod_line_release(line_handle)

    def close_chip(self, chip_handle):
        if chip_handle:
            self.lib.gpiod_chip_close(chip_handle)


@dataclass
class _ServoSpec:
    servo_id: int
    chip: str
    line_offset: int
    label: str
    logical_position: int = SERVO_CENTER_POS
    start_position: int = SERVO_CENTER_POS
    target_position: int = SERVO_CENTER_POS
    move_started_at: float = 0.0
    move_ends_at: float = 0.0
    signal_until: float = 0.0
    chip_handle: Optional[int] = None
    line_handle: Optional[int] = None


class _ServoGPIOStatus:
    def __init__(self, controller: "GPIOServoController"):
        self._controller = controller

    def status(self) -> Dict[str, Any]:
        return self._controller.device_status()


class GPIOServoController:
    """
    Standard 50 Hz servo controller for GPIO-attached hobby servos.

    The public API mirrors the earlier serial-servo backend so the HTTP routes
    and turret state handling can stay unchanged.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ready = False
        self._lib = _LibGpiod()
        self._period_s = SERVO_PWM_PERIOD_US / 1_000_000.0
        self._idle_release_s = max(0.0, SERVO_IDLE_RELEASE_MS / 1000.0)
        self._hold_active_when_idle = bool(SERVO_HOLD_ACTIVE_WHEN_IDLE)
        self.last_command: Dict[str, Any] = {}
        self.last_error: Optional[str] = None
        self.line = _ServoGPIOStatus(self)
        self._servos: Dict[int, _ServoSpec] = {
            PAN_SERVO_ID: _ServoSpec(
                servo_id=PAN_SERVO_ID,
                chip=PAN_SERVO_GPIO_CHIP,
                line_offset=PAN_SERVO_GPIO_LINE,
                label="pan",
            ),
            TILT_SERVO_ID: _ServoSpec(
                servo_id=TILT_SERVO_ID,
                chip=TILT_SERVO_GPIO_CHIP,
                line_offset=TILT_SERVO_GPIO_LINE,
                label="tilt",
            ),
        }
        self._thread = threading.Thread(target=self._pwm_loop, name="gpio-servo-pwm", daemon=True)
        try:
            self._open_lines()
        except Exception:
            pass
        self._thread.start()

    def _open_lines(self):
        opened_chips: Dict[str, int] = {}
        try:
            for spec in self._servos.values():
                chip_handle = opened_chips.get(spec.chip)
                if chip_handle is None:
                    chip_handle = self._lib.open_chip(spec.chip)
                    opened_chips[spec.chip] = chip_handle
                line_handle = self._lib.get_line(chip_handle, spec.line_offset)
                self._lib.request_output(line_handle, f"rov-servo-{spec.label}", 0)
                spec.chip_handle = chip_handle
                spec.line_handle = line_handle
            self._ready = True
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self._ready = False
            self._release_lines()
            raise

    def _release_lines(self):
        seen_chips = set()
        for spec in self._servos.values():
            if spec.line_handle:
                self._lib.release_line(spec.line_handle)
                spec.line_handle = None
            if spec.chip_handle and spec.chip_handle not in seen_chips:
                seen_chips.add(spec.chip_handle)
                self._lib.close_chip(spec.chip_handle)
            spec.chip_handle = None

    def _ensure_ready(self):
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            self._open_lines()

    def _clamp_position(self, position: int) -> int:
        return max(SERVO_MIN_POS, min(SERVO_MAX_POS, int(position)))

    def _position_to_pulse_us(self, position: int) -> int:
        span = SERVO_MAX_POS - SERVO_MIN_POS
        if span <= 0:
            return SERVO_PWM_MIN_US
        fraction = (self._clamp_position(position) - SERVO_MIN_POS) / float(span)
        pulse = SERVO_PWM_MIN_US + ((SERVO_PWM_MAX_US - SERVO_PWM_MIN_US) * fraction)
        return int(round(pulse))

    def _current_position_for(self, spec: _ServoSpec, now: float) -> int:
        if spec.move_ends_at <= spec.move_started_at or now >= spec.move_ends_at:
            spec.logical_position = spec.target_position
            return spec.logical_position

        progress = (now - spec.move_started_at) / (spec.move_ends_at - spec.move_started_at)
        progress = max(0.0, min(1.0, progress))
        pos = spec.start_position + ((spec.target_position - spec.start_position) * progress)
        spec.logical_position = int(round(pos))
        return spec.logical_position

    def _set_line_value(self, spec: _ServoSpec, value: int):
        if not spec.line_handle:
            raise RuntimeError(f"{spec.label} GPIO line is not ready")
        self._lib.set_value(spec.line_handle, value)

    def _signal_active_for(self, spec: _ServoSpec, now: float) -> bool:
        if self._hold_active_when_idle:
            return True
        return now < spec.signal_until

    def _pwm_loop(self):
        while not self._stop.is_set():
            cycle_started = time.monotonic()
            try:
                self._ensure_ready()
                with self._lock:
                    active: List[tuple[_ServoSpec, int]] = []
                    for spec in self._servos.values():
                        current_position = self._current_position_for(spec, cycle_started)
                        if self._signal_active_for(spec, cycle_started):
                            active.append((spec, self._position_to_pulse_us(current_position)))

                if not active:
                    time.sleep(self._period_s)
                    continue

                for spec, _pulse_us in active:
                    self._set_line_value(spec, 1)

                elapsed_us = 0
                for spec, pulse_us in sorted(active, key=lambda item: item[1]):
                    delta_us = max(0, pulse_us - elapsed_us)
                    if delta_us:
                        time.sleep(delta_us / 1_000_000.0)
                    self._set_line_value(spec, 0)
                    elapsed_us = pulse_us

                cycle_elapsed = time.monotonic() - cycle_started
                remaining = self._period_s - cycle_elapsed
                if remaining > 0:
                    time.sleep(remaining)
            except Exception as exc:
                self.last_error = str(exc)
                time.sleep(self._period_s)

    def move(self, servo_id: int, position: int, time_ms: int = 1000) -> Dict[str, Any]:
        servo_id = int(servo_id)
        if servo_id not in self._servos:
            raise ValueError(f"unknown servo id: {servo_id}")
        self._ensure_ready()

        position = self._clamp_position(position)
        time_ms = max(0, min(30_000, int(time_ms)))
        now = time.monotonic()

        with self._lock:
            spec = self._servos[servo_id]
            current_position = self._current_position_for(spec, now)
            spec.start_position = current_position
            spec.logical_position = current_position
            spec.target_position = position
            spec.move_started_at = now
            spec.move_ends_at = now + (time_ms / 1000.0) if time_ms > 0 else now
            spec.signal_until = spec.move_ends_at + self._idle_release_s

        self.last_command = {
            "servo_id": servo_id,
            "position": position,
            "time_ms": time_ms,
            "pulse_us": self._position_to_pulse_us(position),
            "chip": spec.chip,
            "line": spec.line_offset,
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
            now = time.monotonic()
            positions = {
                spec.label: {
                    "servo_id": spec.servo_id,
                    "position": self._current_position_for(spec, now),
                    "target_position": spec.target_position,
                    "chip": spec.chip,
                    "line": spec.line_offset,
                }
                for spec in self._servos.values()
            }

        return {
            "mode": "gpio_pwm",
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
            "servos": positions,
        }

    def device_status(self) -> Dict[str, Any]:
        try:
            self._ensure_ready()
        except Exception:
            pass
        return {
            "name": "servo",
            "mode": "gpio_pwm",
            "ready": self._ready,
            "last_error": self.last_error,
            "pins": {
                "pan": {
                    "header_pin": 21,
                    "chip": PAN_SERVO_GPIO_CHIP,
                    "line": PAN_SERVO_GPIO_LINE,
                },
                "tilt": {
                    "header_pin": 23,
                    "chip": TILT_SERVO_GPIO_CHIP,
                    "line": TILT_SERVO_GPIO_LINE,
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
                    if spec.line_handle:
                        self._lib.set_value(spec.line_handle, 0)
                except Exception:
                    pass
            self._release_lines()
            self._ready = False
