import ctypes
import ctypes.util
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from config import (
    POWER_SOURCE_RELAY_ACTIVE_HIGH,
    POWER_SOURCE_RELAY_GPIO_CHIP,
    POWER_SOURCE_RELAY_GPIO_LINE,
    MOTOR_ENABLE_RELAY_ACTIVE_HIGH,
    MOTOR_ENABLE_RELAY_GPIO_CHIP,
    MOTOR_ENABLE_RELAY_GPIO_LINE,
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

    def request_output(self, line_handle, consumer: str, initial_value: int):
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
class _RelaySpec:
    key: str
    label: str
    chip: Optional[str]
    line_offset: Optional[int]
    active_high: bool
    state: bool = False
    available: bool = False
    chip_handle: Optional[int] = None
    line_handle: Optional[int] = None
    last_error: Optional[str] = None


class RelayController:
    def __init__(self):
        self._lock = threading.RLock()
        self._lib: Optional[_LibGpiod] = None
        self.last_error: Optional[str] = None
        self._relays = {
            "motor_enable": _RelaySpec(
                key="motor_enable",
                label="Motor Enable",
                chip=MOTOR_ENABLE_RELAY_GPIO_CHIP,
                line_offset=MOTOR_ENABLE_RELAY_GPIO_LINE,
                active_high=bool(MOTOR_ENABLE_RELAY_ACTIVE_HIGH),
            ),
            "power_source": _RelaySpec(
                key="power_source",
                label="Shore Power Select",
                chip=POWER_SOURCE_RELAY_GPIO_CHIP,
                line_offset=POWER_SOURCE_RELAY_GPIO_LINE,
                active_high=bool(POWER_SOURCE_RELAY_ACTIVE_HIGH),
            ),
        }
        self._initialize()

    def _initialize(self):
        configured = [
            spec for spec in self._relays.values()
            if spec.chip and spec.line_offset is not None
        ]
        if not configured:
            self.last_error = "relay GPIO lines not configured"
            return

        self._lib = _LibGpiod()
        opened_chips: Dict[str, int] = {}

        try:
            for spec in configured:
                chip_handle = opened_chips.get(spec.chip)
                if chip_handle is None:
                    chip_handle = self._lib.open_chip(spec.chip)
                    opened_chips[spec.chip] = chip_handle
                line_handle = self._lib.get_line(chip_handle, int(spec.line_offset))
                self._lib.request_output(line_handle, f"rov-{spec.key}", self._raw_value(spec, False))
                spec.chip_handle = chip_handle
                spec.line_handle = line_handle
                spec.available = True
                spec.last_error = None
                spec.state = False
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self._release_lines()
            for spec in self._relays.values():
                if spec.chip and spec.line_offset is not None:
                    spec.last_error = str(exc)

    def _release_lines(self):
        if not self._lib:
            return
        seen_chips = set()
        for spec in self._relays.values():
            if spec.line_handle:
                self._lib.release_line(spec.line_handle)
                spec.line_handle = None
            if spec.chip_handle and spec.chip_handle not in seen_chips:
                seen_chips.add(spec.chip_handle)
                self._lib.close_chip(spec.chip_handle)
            spec.chip_handle = None
            spec.available = False

    def _raw_value(self, spec: _RelaySpec, enabled: bool) -> int:
        if spec.active_high:
            return 1 if enabled else 0
        return 0 if enabled else 1

    def _spec_to_dict(self, spec: _RelaySpec) -> Dict[str, Any]:
        return {
            "key": spec.key,
            "label": spec.label,
            "configured": spec.chip is not None and spec.line_offset is not None,
            "available": spec.available,
            "active_high": spec.active_high,
            "enabled": spec.state,
            "chip": spec.chip,
            "line": spec.line_offset,
            "last_error": spec.last_error,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "available": any(spec.available for spec in self._relays.values()),
                "ok": self.last_error is None,
                "last_error": self.last_error,
                "motor_enable": self._spec_to_dict(self._relays["motor_enable"]),
                "power_source": {
                    **self._spec_to_dict(self._relays["power_source"]),
                    "selected_source": (
                        "shore"
                        if self._relays["power_source"].state
                        else "battery"
                    ),
                    "battery_isolated": self._relays["power_source"].state,
                    "shore_isolated": not self._relays["power_source"].state,
                },
            }

    def set_state(self, key: str, enabled: bool) -> Dict[str, Any]:
        if key not in self._relays:
            raise KeyError(key)

        with self._lock:
            spec = self._relays[key]
            if not spec.available or not spec.line_handle or not self._lib:
                raise RuntimeError(spec.last_error or f"{spec.label} relay unavailable")
            self._lib.set_value(spec.line_handle, self._raw_value(spec, bool(enabled)))
            spec.state = bool(enabled)
            spec.last_error = None
            self.last_error = None
            return self._spec_to_dict(spec)

    def is_motor_enabled(self) -> Optional[bool]:
        """Return motor-enable state when the relay is available, else None."""
        with self._lock:
            spec = self._relays["motor_enable"]
            if not spec.available:
                return None
            return bool(spec.state)

    def disable_motor(self) -> None:
        """Best-effort fail-safe used by the drive supervisor."""
        try:
            self.set_state("motor_enable", False)
        except Exception:
            pass
