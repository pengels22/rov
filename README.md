# ROV Control Stack

This repository contains the Raspberry Pi backend and Arduino firmware used to control the ROV drive system, turret, servos, relays, and attached sensors.

## Repo Layout

- `backend/` - Python HTTP API and hardware control code that runs on the Pi
- `firmware/drive_nano/` - drive controller firmware
- `firmware/turret_xiao/` - turret controller firmware
- `firmware/turret_servos/` - dedicated servo controller firmware
- `firmware/deploy.py` - compile/upload helper for the firmware targets and Pi service restart
- `config/` - runtime config files such as saved turret IP state
- `Templates/` - frontend template served by the backend

## What The Backend Does

The backend exposes a small HTTP API for:

- drive commands
- turret status and telemetry
- servo movement
- relay-based power control
- Pi ultrasonic sensor reads
- LiDAR status

The main entrypoint is [backend/app.py](/home/pi/ROV/backend/app.py), and the shipped systemd unit is [backend/rov-backend.service](/home/pi/ROV/backend/rov-backend.service).

## Requirements

Pi-side runtime:

- Python 3
- `pyserial`
- `libgpiod` tools such as `gpiomon` and `gpioset` for GPIO-backed relays and ultrasonics

Firmware deployment:

- `arduino-cli`
- board packages for:
  - `arduino:esp32:nano_nora`
  - `esp32:esp32:XIAO_ESP32S3`
  - `SparkFun:avr:promicro`

## Configuration

Hardware serial ports, HTTP bind settings, relay GPIO assignments, and ultrasonic line mappings live in [backend/config.py](/home/pi/ROV/backend/config.py).

Before running on different hardware, review at least:

- `DRIVE_PORT`
- `TURRET_PORT`
- `SERVO_PORT`
- `HTTP_HOST`
- `HTTP_PORT`
- ultrasonic chip and line values
- relay chip and line values

## Running The Backend

From the repo root:

```bash
cd /home/pi/ROV/backend
python3 app.py --host 0.0.0.0 --port 8080
```

Quick checks:

```bash
curl http://ROV.local:8080/api/status
curl http://ROV.local:8080/api/drive/status
curl http://ROV.local:8080/api/turret/telemetry
curl http://ROV.local:8080/api/servo/status
```

## Installing As A Service

The included service file expects this checkout to live at `/home/pi/ROV`.

Install and enable it with:

```bash
sudo cp /home/pi/ROV/backend/rov-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rov-backend.service
```

Useful commands:

```bash
sudo systemctl status rov-backend.service
sudo journalctl -u rov-backend.service -f
```

## Firmware Deployment

Use [firmware/deploy.py](/home/pi/ROV/firmware/deploy.py) from the repo root.

Compile and upload a target:

```bash
python3 firmware/deploy.py drive
python3 firmware/deploy.py turret
```

Compile only:

```bash
python3 firmware/deploy.py --compile-only drive
python3 firmware/deploy.py --compile-only turret
```

Legacy explicit form:

```bash
python3 firmware/deploy.py upload drive
python3 firmware/deploy.py compile turret
```

Restart the Pi backend service only:

```bash
python3 firmware/deploy.py pi
```

Notes:

- uploading the `turret` target stops `rov-backend.service` before flashing and starts it again afterward
- the script looks for board ports using `/dev/rov/...` and `/dev/serial/by-id/...`
- the Pro Micro servo board uses a 1200 bps touch/reset flow before upload

## Common API Endpoints

- `GET /api/status`
- `GET /api/drive/status`
- `POST /api/drive/joy`
- `POST /api/drive/move`
- `POST /api/drive/stop`
- `POST /api/drive/leds`
- `GET /api/turret/status`
- `GET /api/turret/telemetry`
- `POST /api/turret/aim`
- `POST /api/turret/tilt_zero`
- `GET /api/power/status`
- `POST /api/power/motor_enable`
- `POST /api/power/battery_kill`
- `GET /api/servo/status`
- `POST /api/servo/move`
- `POST /api/servo/center`

## Project Notes

- `config/turret_ip.txt` is runtime state written by the system and should not be treated like a stable source file
- the backend currently assumes the checkout path `/home/pi/ROV` in a few places, including the systemd unit and turret IP file lookup
- [backend/README.md](/home/pi/ROV/backend/README.md) contains additional hardware-specific usage examples
