#!/usr/bin/env python3
"""
Monitor turret serial for IP announcements and send WiFi credentials over serial.
Saves the last seen IP to /home/pi/ROV/config/turret_ip.txt

Usage:
  python3 turret_serial.py --monitor
  python3 turret_serial.py --set-wifi SSID PASSWORD
"""
import argparse
import os
import serial
import time

PORT = '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_8C:BF:EA:8F:4C:18-if00'
BAUD = 115200
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
            # Look for explicit IP announcement or OK,WIFI_STA,<ip>
            if line.startswith('IP,'):
                ip = line.split(',', 1)[1]
                save_ip(ip)
            elif line.startswith('OK,WIFI_STA,'):
                parts = line.split(',')
                if len(parts) >= 3:
                    ip = parts[2]
                    save_ip(ip)


def set_wifi(ssid, password, timeout=10):
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
    g.add_argument('--monitor', action='store_true', help='Monitor serial and save IP announcements')
    g.add_argument('--set-wifi', nargs=2, metavar=('SSID', 'PASS'), help='Send WiFi credentials to turret')
    args = p.parse_args()

    if args.monitor:
        monitor()
    elif args.set_wifi:
        ssid, pw = args.set_wifi
        set_wifi(ssid, pw)
