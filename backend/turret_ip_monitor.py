#!/usr/bin/env python3
"""
Keep config/turret_ip.txt synced with the turret IP reported by the backend API.

This avoids opening /dev/rov/turret from a second process, which can interfere
with the main backend's serial connection.
"""

import json
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


STATUS_URL = "http://127.0.0.1:8080/api/turret/status"
SAVE_FILE = Path("/home/pi/ROV/config/turret_ip.txt")
POLL_INTERVAL_S = 2.0


def save_ip(ip: str) -> None:
    ip = (ip or "").strip()
    if not ip:
        return
    SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAVE_FILE.write_text(ip + "\n", encoding="utf-8")
    print(f"Saved turret IP: {ip} -> {SAVE_FILE}", flush=True)


def fetch_ip() -> str | None:
    with urlopen(STATUS_URL, timeout=1.5) as response:
        payload = json.load(response)
    if isinstance(payload, dict) and payload.get("ok") is False:
        return None
    if isinstance(payload, dict):
        ip = payload.get("ip")
        if isinstance(ip, str) and ip.strip():
            return ip.strip()
    return None


def main() -> None:
    last_ip = None
    while True:
        try:
            ip = fetch_ip()
            if ip and ip != last_ip:
                save_ip(ip)
                last_ip = ip
        except URLError:
            pass
        except Exception as exc:
            print(f"turret_ip_monitor error: {exc}", flush=True)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
