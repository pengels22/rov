#!/usr/bin/env python3
import argparse
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import serial  # type: ignore
except Exception:
    serial = None


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent
ARDUINO_CLI = Path(os.environ.get("ARDUINO_CLI", str(Path.home() / ".local/bin/arduino-cli")))

DRIVE_NANO_PORTS = [
    "/dev/rov/drive",
]
TURRET_XIAO_PORTS = [
    "/dev/rov/turret",
]
FLAG_TARGETS = {
    "turret_xiao": "turret",
    "drive_nano": "drive",
    "backend": "pi",
}

SERIAL_MONITOR_FLAGS = [
    {
        "options": ("-ts",),
        "dest": "turret_serial",
        "target": "turret",
        "help": "Print serial output from the turret XIAO controller.",
    },
    {
        "options": ("-ds",),
        "dest": "drive_serial",
        "target": "drive",
        "help": "Print serial output from the drive Nano ESP32 controller.",
    },
]

TARGET_FLAGS = [
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
                "name": "turret-xiao",
                "sketch": PROJECT_ROOT / "firmware/turret_xiao",
                "fqbn": "esp32:esp32:XIAO_ESP32S3:PSRAM=opi",
                "ports": TURRET_XIAO_PORTS,
                "stop_services": [
                    "rov-backend.service",
                ],
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
            "Examples: `./deploy -d`, "
            "`./deploy -t`, "
            "`./deploy -ts`, "
            "`./deploy -ds`, "
            "`./deploy -bl`, "
            "`./deploy -b`, "
            "`./deploy -a`. "
            "Deploy/restart actions run `git pull --ff-only` first."
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
    for flag in SERIAL_MONITOR_FLAGS:
        parser.add_argument(
            *flag["options"],
            dest=flag["dest"],
            action="store_true",
            help=flag["help"],
        )
    parser.add_argument(
        "-bl",
        "--backend-logs",
        action="store_true",
        help="Follow the backend systemd service logs.",
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
    parser.add_argument(
        "--no-pull",
        action="store_true",
        help="Skip the automatic `git pull --ff-only` before deploy/restart actions.",
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
    if args.backend_logs:
        if (
            collect_flag_targets(args)
            or args.all
            or args.compile_only
            or args.arg1
            or args.arg2
            or any(getattr(args, flag["dest"]) for flag in SERIAL_MONITOR_FLAGS)
        ):
            raise SystemExit("Backend logs cannot be combined with deploy or serial options.")
        return "logs", ["rov-backend.service"]

    serial_targets = [
        flag["target"]
        for flag in SERIAL_MONITOR_FLAGS
        if getattr(args, flag["dest"])
    ]
    if serial_targets:
        if len(serial_targets) > 1:
            raise SystemExit("Choose only one serial monitor flag at a time.")
        if collect_flag_targets(args) or args.all or args.compile_only or args.arg1 or args.arg2:
            raise SystemExit("Serial monitor flags cannot be combined with deploy targets.")
        return "serial", serial_targets

    flag_targets = collect_flag_targets(args)
    if flag_targets:
        if args.arg1 or args.arg2:
            raise SystemExit("Use either short flags or legacy positional targets, not both.")
        mode = "compile" if args.compile_only else "upload"
        return mode, flag_targets

    if not args.arg1:
        raise SystemExit(
            "Choose a target: -t, -d, -b, -a; "
            "a serial monitor: -ts, -ds; backend logs: -bl; "
            "or a legacy target name."
        )

    if args.arg1 in ("compile", "upload"):
        if not args.arg2:
            raise SystemExit("Usage: ./deploy compile|upload drive|turret")
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


def pull_latest() -> None:
    if not (PROJECT_ROOT / ".git").exists():
        print("[git] Skipping pull; project root is not a git checkout.")
        return
    print("[git] Pulling latest changes before deploy...")
    run_command(["git", "-C", str(PROJECT_ROOT), "pull", "--ff-only"], "git")
    print("[git] Pull complete.")


def manage_service(service: str, action: str) -> None:
    cmd = ["sudo", "systemctl", action, service]
    print(f"[service:{action}] {' '.join(cmd)}")
    completed = subprocess.run(cmd, text=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def service_is_active(service: str) -> bool:
    completed = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


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


def monitor_serial(target: str) -> None:
    if serial is None:
        raise SystemExit("pyserial is required for serial monitoring.")

    board = BOARD_CONFIG[target]["boards"][0]
    port = find_port(board["name"], board["ports"])
    services = list(board.get("stop_services", []))
    if "rov-backend.service" not in services:
        services.append("rov-backend.service")

    active_services = [service for service in services if service_is_active(service)]
    for service in active_services:
        manage_service(service, "stop")

    try:
        port = wait_for_port(board["ports"], timeout_s=5.0)
        print(f"[serial:{board['name']}] {port} at 115200 baud (Ctrl+C to exit)")
        with serial.Serial(port, 115200, timeout=0.25) as connection:
            while True:
                raw = connection.readline()
                if raw:
                    print(raw.decode("utf-8", errors="replace"), end="", flush=True)
    except KeyboardInterrupt:
        print("\n[serial] Monitor stopped.")
    finally:
        for service in reversed(active_services):
            manage_service(service, "start")


def follow_backend_logs(service: str) -> None:
    cmd = ["sudo", "journalctl", "-u", service, "-f", "-n", "100"]
    print(f"[logs] {' '.join(cmd)}")
    try:
        subprocess.run(cmd, text=True, check=False)
    except KeyboardInterrupt:
        print("\n[logs] Log follow stopped.")


def wait_for_port(patterns: list[str], timeout_s: float = 10.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        matches = matching_ports(patterns)
        if matches:
            return matches[0]
        time.sleep(0.25)
    return find_port("reappearing-device", patterns)


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

            upload_cmd = [str(ARDUINO_CLI), "upload", "-p", port, "--fqbn", fqbn, sketch]
            run_command(upload_cmd, f"upload:{board['name']}")
    finally:
        for service in reversed(target_services):
            manage_service(service, "start")

    print(f"Upload succeeded for `{target}`.")


def main() -> int:
    args = parse_args()
    mode, targets = resolve_mode_and_targets(args)

    if mode == "serial":
        monitor_serial(targets[0])
        return 0
    if mode == "logs":
        follow_backend_logs(targets[0])
        return 0

    if not args.no_pull:
        pull_latest()

    for target in targets:
        deploy_target(mode, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
