#!/usr/bin/env python3
"""
Utilities for talking to the turret over its serial port.

`--monitor` is useful for short manual debugging sessions, but it should not be
run alongside the main backend because both processes would compete for the same
serial device. The long-running systemd service uses `turret_ip_monitor.py`
instead, which reads the turret IP from the local backend API and keeps
config/turret_ip.txt up to date without opening the serial port directly.

Usage:
  python3 turret_serial.py --monitor
  python3 turret_serial.py --set-wifi SSID PASSWORD
"""
import argparse
import os
import serial
import time

from config import TURRET_BAUD, TURRET_PORT

PORT = TURRET_PORT
BAUD = TURRET_BAUD
SAVE_PATH = '/home/pi/ROV/config'
SAVE_FILE = os.path.join(SAVE_PATH, 'turret_ip.txt')


def ensure_save_dir():
    os.makedirs(SAVE_PATH, exist_ok=True)


def save_ip(ip):
    ensure_save_dir()
    with open(SAVE_FILE, 'w') as f:
        f.write(ip.strip() + '\n')
    print(f"Saved turret IP: {ip} -> {SAVE_FILE}")


def monitor():
    print(f"Opening serial {PORT} @ {BAUD}")
    with serial.Serial(PORT, BAUD, timeout=1) as ser:
        while True:
            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
            except Exception as e:
                print('Serial read error:', e)
                time.sleep(1)
                continue
            if not line:
                continue
            print(line)
            # Look for explicit IP announcement, wifi setup confirmation,
            # or periodic STATUS lines that include the current IP.
            if line.startswith('IP,'):
                ip = line.split(',', 1)[1]
                save_ip(ip)
            elif line.startswith('OK,WIFI_STA,'):
                parts = line.split(',')
                if len(parts) >= 3:
                    ip = parts[2]
                    save_ip(ip)
            elif line.startswith('STATUS,'):
                parts = line.split(',')
                if len(parts) >= 3 and parts[2]:
                    save_ip(parts[2])


def set_wifi(ssid, password, timeout=20):
    cmd = f"SET_WIFI,{ssid},{password}\n"
    print(f"Sending credentials to {PORT}")
    with serial.Serial(PORT, BAUD, timeout=1) as ser:
        ser.write(cmd.encode('utf-8'))
        ser.flush()
        # wait for response
        start = time.time()
        while time.time() - start < timeout:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(line)
                if line.startswith('OK,WIFI_SET') or line.startswith('ERR,WIFI_SET'):
                    return
        print('No final response received (timeout)')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--monitor', action='store_true', help='Manually monitor serial and save IP announcements')
    g.add_argument('--set-wifi', nargs=2, metavar=('SSID', 'PASS'), help='Send WiFi credentials to turret')
    args = p.parse_args()

    if args.monitor:
        monitor()
    elif args.set_wifi:
        ssid, pw = args.set_wifi
        set_wifi(ssid, pw)
