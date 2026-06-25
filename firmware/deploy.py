#!/usr/bin/env python3
import argparse
import glob
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import serial  # type: ignore
except Exception:
    serial = None


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
ARDUINO_CLI = Path(os.environ.get("ARDUINO_CLI", str(Path.home() / ".local/bin/arduino-cli")))

DRIVE_NANO_PORTS = [
    "/dev/rov/drive",
    "/dev/serial/by-id/usb-Arduino_Nano_ESP32_*",
]
TURRET_NANO_PORTS = [
    "/dev/serial/by-id/usb-Arduino_LLC_Arduino_Leonardo-if00",
]
TURRET_XIAO_PORTS = [
    "/dev/rov/turret",
    "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*",
]
RESET_RELAY_CMD = "gpioset gpiochip3 5=1; sleep 0.2; gpioset gpiochip3 5=0"

FLAG_TARGETS = {
    "turret_nano": "turret",
    "turret_xiao": "turretn",
    "drive_nano": "drive",
    "backend": "pi",
}

TARGET_FLAGS = [
    {
        "options": ("-tn", "--turret-nano"),
        "dest": "turret_nano",
        "help": "Build and deploy the turret nano/servo controller.",
    },
    {
        "options": ("-t", "--turret-xiao"),
        "dest": "turret_xiao",
        "help": "Build and deploy the turret XIAO controller.",
    },
    {
        "options": ("-d", "--drive-nano"),
        "dest": "drive_nano",
        "help": "Build and deploy the drive Nano controller.",
    },
    {
        "options": ("-b", "--backend"),
        "dest": "backend",
        "help": "Restart the backend service.",
    },
]

ALL_TARGETS = [
    FLAG_TARGETS["turret_nano"],
    FLAG_TARGETS["turret_xiao"],
    FLAG_TARGETS["drive_nano"],
    FLAG_TARGETS["backend"],
]

BOARD_CONFIG = {
    "drive": {
        "boards": [
            {
                "name": "drive",
                "sketch": PROJECT_ROOT / "firmware/drive_nano",
                "fqbn": "arduino:esp32:nano_nora",
                "ports": DRIVE_NANO_PORTS,
                "stop_services": [
                    "rov-backend.service",
                ],
            },
        ],
    },
    "turret": {
        "boards": [
            {
                "name": "turret-servos",
                "sketch": PROJECT_ROOT / "firmware/turret_servos",
                "fqbn": "SparkFun:avr:promicro:cpu=16MHzatmega32U4",
                "ports": TURRET_NANO_PORTS,
                "touch_1200bps": True,
                "usb_reenumerate": True,
            },
        ],
    },
    "turretn": {
        "boards": [
            {
                "name": "turret-xiao",
                "sketch": PROJECT_ROOT / "firmware/turret_xiao",
                "fqbn": "esp32:esp32:XIAO_ESP32S3",
                "ports": TURRET_XIAO_PORTS,
                "stop_services": [
                    "rov-backend.service",
                ],
                # Optional command to toggle an external relay/reset line before upload.
                # Can be a string (shell command) or a list (argv style).
                # Example using libgpiod `gpioset` to toggle gpiochip3 line 5 (GPIO3_A5):
                # "reset_cmd": "gpioset gpiochip3 5=1; sleep 0.2; gpioset gpiochip3 5=0",
                "reset_cmd": RESET_RELAY_CMD,
            },
        ],
    },
}

SERVICE_TARGETS = {
    "pi": [
        "rov-backend.service",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compile and optionally upload Arduino firmware for a configured target. "
            "Examples: `python3 firmware/deploy.py -d`, "
            "`python3 firmware/deploy.py -t`, "
            "`python3 firmware/deploy.py -tn`, "
            "`python3 firmware/deploy.py -b`, "
            "`python3 firmware/deploy.py -a`."
        )
    )
    parser.add_argument(
        "arg1",
        nargs="?",
        help="Target (`drive`, `turret`, or `pi`) or legacy action (`compile` or `upload`).",
    )
    parser.add_argument(
        "arg2",
        nargs="?",
        help="Legacy target when using `compile TARGET` or `upload TARGET`.",
    )
    for flag in TARGET_FLAGS:
        parser.add_argument(
            *flag["options"],
            dest=flag["dest"],
            action="store_true",
            help=flag["help"],
        )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Build and deploy all firmware targets, then restart the backend service.",
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Compile the sketch and stop before upload.",
    )
    return parser.parse_args()


def collect_flag_targets(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(ALL_TARGETS)

    selected: list[str] = []
    for flag_name, target in FLAG_TARGETS.items():
        if getattr(args, flag_name) and target not in selected:
            selected.append(target)
    return selected


def resolve_mode_and_targets(args: argparse.Namespace) -> tuple[str, list[str]]:
    flag_targets = collect_flag_targets(args)
    if flag_targets:
        if args.arg1 or args.arg2:
            raise SystemExit("Use either short flags or legacy positional targets, not both.")
        mode = "compile" if args.compile_only else "upload"
        return mode, flag_targets

    if not args.arg1:
        raise SystemExit("Choose a target: -tn, -t, -d, -b, -a, or a legacy target name.")

    if args.arg1 in ("compile", "upload"):
        if not args.arg2:
            raise SystemExit("Usage: python3 firmware/deploy.py compile|upload drive|turret")
        mode = args.arg1
        targets = [args.arg2]
    else:
        mode = "compile" if args.compile_only else "upload"
        targets = [args.arg1]

    for target in targets:
        if target in SERVICE_TARGETS:
            continue

        if target not in BOARD_CONFIG:
            valid = ", ".join(sorted({*BOARD_CONFIG, *SERVICE_TARGETS}))
            raise SystemExit(f"Unknown target `{target}`. Valid targets: {valid}")

    return mode, targets


def target_mode(mode: str, target: str) -> str:
    if target in SERVICE_TARGETS:
        return "service"
    return mode


def matching_ports(patterns: list[str]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        for match in sorted(glob.glob(pattern)):
            if match not in matches:
                matches.append(match)
    return matches


def find_port(target: str, patterns: list[str]) -> str:
    matches = matching_ports(patterns)
    if matches:
        return matches[0]

    checked = list(patterns)
    checked_lines = "\n".join(f"  {item}" for item in checked)
    raise SystemExit(f"No serial port found for target `{target}`.\nChecked:\n{checked_lines}")


def run_command(cmd: list[str], label: str) -> None:
    print(f"[{label}] {' '.join(cmd)}")
    completed = subprocess.run(cmd, text=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def manage_service(service: str, action: str) -> None:
    cmd = ["sudo", "systemctl", action, service]
    print(f"[service:{action}] {' '.join(cmd)}")
    completed = subprocess.run(cmd, text=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def collect_target_services(boards: list[dict]) -> list[str]:
    ordered: list[str] = []
    for board in boards:
        for service in board.get("stop_services", []):
            if service not in ordered:
                ordered.append(service)
    return ordered


def restart_services(services: list[str]) -> None:
    if not services:
        print("No services configured for this target.")
        return

    for service in services:
        manage_service(service, "stop")
    time.sleep(1.0)
    for service in reversed(services):
        manage_service(service, "start")


def touch_serial_1200bps(port: str) -> None:
    if serial is None:
        print("[touch1200] pyserial not available; skipping explicit 1200 bps reset")
        return

    print(f"[touch1200] Opening {port} at 1200 bps to trigger bootloader")
    try:
        ser = serial.Serial(port, 1200, timeout=1)
        ser.setDTR(False)
        time.sleep(0.2)
        ser.close()
    except Exception as exc:
        print(f"[touch1200] Warning: explicit 1200 bps reset failed: {exc}")


def wait_for_ports_to_clear(patterns: list[str], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not matching_ports(patterns):
            return
        time.sleep(0.25)

    print("[serial] Warning: port did not disappear before timeout; continuing")


def wait_for_port(patterns: list[str], timeout_s: float = 10.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        matches = matching_ports(patterns)
        if matches:
            return matches[0]
        time.sleep(0.25)
    return find_port("reappearing-device", patterns)


def resolve_usb_device_id(port: str) -> str | None:
    try:
        tty_name = Path(os.path.realpath(port)).name
        sys_tty = Path(f"/sys/class/tty/{tty_name}/device").resolve()
    except Exception:
        return None

    candidates = [sys_tty, *sys_tty.parents]
    pattern = re.compile(r"^\d+-\d+(?:\.\d+)*$")
    for candidate in candidates:
        if pattern.match(candidate.name):
            return candidate.name
    return None


def reenumerate_usb_device(port: str) -> None:
    device_id = resolve_usb_device_id(port)
    if not device_id:
        print(f"[usb-reset] Could not resolve USB device for {port}; skipping re-enumeration")
        return

    authorized_path = Path(f"/sys/bus/usb/devices/{device_id}/authorized")
    if not authorized_path.exists():
        print(f"[usb-reset] {authorized_path} not present; skipping re-enumeration")
        return

    print(f"[usb-reset] Re-enumerating USB device {device_id} for {port}")
    try:
        subprocess.run(
            ["sudo", "sh", "-c", f"echo 0 > {authorized_path}"],
            check=True,
            text=True,
        )
        time.sleep(1.0)
        subprocess.run(
            ["sudo", "sh", "-c", f"echo 1 > {authorized_path}"],
            check=True,
            text=True,
        )
        time.sleep(2.0)
    except Exception as exc:
        print(f"[usb-reset] Warning: USB re-enumeration failed: {exc}")


def deploy_target(mode: str, target: str) -> None:
    effective_mode = target_mode(mode, target)
    if effective_mode == "service":
        print(f"Target: {target}")
        restart_services(SERVICE_TARGETS[target])
        print(f"Service restart succeeded for `{target}`.")
        return

    if not ARDUINO_CLI.exists():
        raise SystemExit(f"arduino-cli not found at {ARDUINO_CLI}")

    print(f"Target: {target}")
    boards = BOARD_CONFIG[target]["boards"]
    target_services = collect_target_services(boards)
    for board in boards:
        sketch = str(board["sketch"])
        fqbn = board["fqbn"]
        print(f"Board:  {board['name']}")
        print(f"Sketch: {sketch}")
        print(f"FQBN:   {fqbn}")
        compile_cmd = [str(ARDUINO_CLI), "compile", "--fqbn", fqbn, sketch]
        run_command(compile_cmd, f"compile:{board['name']}")

    if effective_mode == "compile":
        print("Compile succeeded. Skipping upload.")
        return

    try:
        for service in target_services:
            manage_service(service, "stop")
        if target_services:
            time.sleep(1.0)

        for board in boards:
            port = find_port(board["name"], board["ports"])
            sketch = str(board["sketch"])
            fqbn = board["fqbn"]
            print(f"Upload port for {board['name']}: {port}")

            if board.get("usb_reenumerate"):
                reenumerate_usb_device(port)
                port = wait_for_port(board["ports"])
                print(f"USB device ready for {board['name']}: {port}")

            if board.get("touch_1200bps"):
                touch_serial_1200bps(port)
                wait_for_ports_to_clear(board["ports"])
                port = wait_for_port(board["ports"])
                print(f"Bootloader/serial port ready for {board['name']}: {port}")

            # Optional: run a reset/toggle command (e.g., flip a relay) to reset the target board.
            # Run this after compile and before upload, then wait for the serial port to reappear.
            if board.get("reset_cmd"):
                rc = board["reset_cmd"]
                if isinstance(rc, str):
                    run_command(["sh", "-c", rc], f"reset:{board['name']}")
                else:
                    run_command(rc, f"reset:{board['name']}")

                # Give the device a moment to reset and re-enumerate, then wait for the port.
                wait_for_ports_to_clear(board["ports"])
                port = wait_for_port(board["ports"], timeout_s=10.0)
                print(f"Reset/toggle complete, device ready for {board['name']}: {port}")

            upload_cmd = [str(ARDUINO_CLI), "upload", "-p", port, "--fqbn", fqbn, sketch]
            run_command(upload_cmd, f"upload:{board['name']}")
    finally:
        for service in reversed(target_services):
            manage_service(service, "start")

    print(f"Upload succeeded for `{target}`.")


def main() -> int:
    args = parse_args()
    mode, targets = resolve_mode_and_targets(args)

    for target in targets:
        deploy_target(mode, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
