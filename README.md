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
- `rpicam-vid` or `libcamera-vid` for the Rock 3C CSI chassis camera stream

Firmware deployment:

- `arduino-cli`
- board packages for:
  - `arduino:esp32:nano_nora`
  - `esp32:esp32:XIAO_ESP32S3`

## Configuration

Hardware serial ports, HTTP bind settings, relay GPIO assignments, and ultrasonic line mappings live in [backend/config.py](/home/pi/ROV/backend/config.py).

Before running on different hardware, review at least:

- `DRIVE_PORT`
- `TURRET_PORT`
- `TURRET_SERVO_PORT`
- `LIDAR_PORT`
- `CHASSIS_CAMERA_ENABLED`
- `CHASSIS_CAMERA_COMMANDS`
- `HTTP_HOST`
- `HTTP_PORT`
- ultrasonic chip and line values
- relay chip and line values

## Hardware Pinouts

These tables reflect the current firmware and backend config.

### Drive Nano ESP32

Source: [firmware/drive_nano/drive_nano.ino](/home/pi/ROV/firmware/drive_nano/drive_nano.ino)

| Function | Nano ESP32 pin | Direction / notes |
|---|---:|---|
| Left encoder A | D2 | `INPUT_PULLUP` |
| Left encoder B | D3 | `INPUT_PULLUP` |
| Right encoder A | D4 | `INPUT_PULLUP` |
| Right encoder B | D5 | `INPUT_PULLUP` |
| Left motor forward input | D6 | Output |
| Left motor reverse input | D7 | Output |
| Right motor forward input | D8 | Output |
| Right motor reverse input | D9 | Output |
| Left motor PWM | D11 | PWM output |
| Right motor PWM | D12 | PWM output |
| Battery voltage sense | A0 | Analog input, divider ratio `5.0` |

| Motor side | Driver inputs | PWM |
|---|---|---|
| Left | D6 / D7 | D11 |
| Right | D8 / D9 | D12 |

### Turret Servo Controller / Pro Micro-Leonardo

Source: [firmware/turret_servos/turret_servos.ino](/home/pi/ROV/firmware/turret_servos/turret_servos.ino)

| Function | Board pin | Direction / notes |
|---|---:|---|
| Tilt servo signal | D3 | Servo output |
| Front NeoPixel strip data | D4 | Two pixels |
| Rear NeoPixel strip data | D5 | Two pixels |
| Pan motor driver IN2 | D6 | PWM-capable output |
| Pan motor driver IN1 | D9 | Digital output |
| Pan home switch | D10 | `INPUT_PULLUP`; pressed reads LOW |
| Battery voltage sense | A0 | Analog input, divider ratio `5.0` |

Pan home-switch wiring:

| Switch terminal | Connection |
|---|---|
| NC | 5 V |
| NO | GND |
| Common | D10 |

Pan motor notes:

- Current clockwise direction is negative speed.
- Pan output is scaled to `75%` in firmware.
- D9 is held digital because the Servo library uses Timer1; PWM is applied on D6.

### Turret XIAO ESP32S3 Sense

Source: [firmware/turret_xiao/turret_xiao.ino](/home/pi/ROV/firmware/turret_xiao/turret_xiao.ino)

| Function | XIAO / ESP32S3 pin | Notes |
|---|---:|---|
| I2C SDA | `SDA` | ToF range sensor bus |
| I2C SCL | `SCL` | ToF range sensor bus |
| VL53L1X ToF address | `0x29` | I2C device address |

Camera pin mapping:

| Camera signal | ESP32S3 GPIO |
|---|---:|
| XCLK | GPIO10 |
| SIOD / SDA | GPIO40 |
| SIOC / SCL | GPIO39 |
| Y9 | GPIO48 |
| Y8 | GPIO11 |
| Y7 | GPIO12 |
| Y6 | GPIO14 |
| Y5 | GPIO16 |
| Y4 | GPIO18 |
| Y3 | GPIO17 |
| Y2 | GPIO15 |
| VSYNC | GPIO38 |
| HREF | GPIO47 |
| PCLK | GPIO13 |
| PWDN | Not used / `-1` |
| RESET | Not used / `-1` |

### Rock 3C / Pi-side GPIO

Source: [backend/config.py](/home/pi/ROV/backend/config.py)

Relay outputs:

| Function | Header pin | BCM | libgpiod chip / line | Active logic |
|---|---:|---:|---|---|
| Motor enable relay K1 | PIN 11 | BCM17 | `gpiochip3` line `1` | Active-low |
| Power-source transfer pair K2 | PIN 13 | BCM27 | `gpiochip3` line `2` | Active-high |

Power-source transfer logic:

| GPIO state | Battery path | Shore-power path |
|---|---|---|
| LOW | Battery enabled | Shore isolated |
| HIGH | Battery isolated | Shore enabled |

Pi ultrasonic sensors:

| Sensor | Signal | Header pin | BCM | libgpiod chip / line |
|---|---|---:|---:|---|
| Front | Trigger | PIN 16 | BCM23 | `gpiochip3` line `9` |
| Front | Echo | PIN 18 | BCM24 | `gpiochip3` line `10` |
| Rear | Trigger | PIN 22 | BCM25 | `gpiochip3` line `17` |
| Rear | Echo | PIN 24 | BCM8 | `gpiochip4` line `22` |

Deploy/reset helper:

| Function | libgpiod chip / line | Notes |
|---|---|---|
| Turret upload reset relay | `gpiochip3` line `5` | Toggled by [firmware/deploy.py](/home/pi/ROV/firmware/deploy.py) before turret upload |

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

Configure the dashboard login:

```bash
sudo tee /etc/rov-backend.env >/dev/null <<'EOF'
ROV_USERNAME=operator
ROV_PASSWORD=choose-a-password
EOF
sudo chmod 600 /etc/rov-backend.env
sudo systemctl restart rov-backend.service
```

Open `http://ROV.local:8080`; the backend redirects unauthenticated browsers
to the login page. Login sessions last 12 hours and are cleared whenever the
backend restarts. Use HTTPS or a trusted isolated network because plain HTTP
does not encrypt the password in transit.

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
python3 firmware/deploy.py -a
```

Print controller serial output (press Ctrl+C to stop and restart the backend):

```bash
python3 firmware/deploy.py -ts   # turret XIAO
python3 firmware/deploy.py -ds   # drive Nano ESP32
python3 firmware/deploy.py -bl   # follow backend service logs
```

If you are already in `/home/pi/ROV/firmware`, run `python3 deploy.py -a`.
For module mode, use `python3 -m deploy -a`; do not include the `.py` suffix.

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
- the script looks for board ports using the `/dev/rov/...` udev aliases
- `turret_servos.ino` is intentionally excluded from CLI deployment and must
  be uploaded manually

## Common API Endpoints

- `GET /api/status`
- `GET /api/logs`
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
- `POST /api/power/shore_power`
- `GET /api/servo/status`
- `POST /api/servo/move`
- `POST /api/servo/center`
- `GET /api/chassis/camera/status`
- `GET /api/chassis/camera/stream`

## Logs

The dashboard has a `Logs` link at the top right. It opens `/logs`, which
shows:

- command/ACK traffic from API commands and serial devices
- backend/system warnings and errors

By default the persistent files are written under `logs/`:

- `logs/commands.log`
- `logs/system.log`

Each file keeps the newest 200 lines and deletes the oldest first. Override
the directory with `ROV_LOG_DIR=/path/to/logs` in `/etc/rov-backend.env`.

## Project Notes

- `config/turret_ip.txt` is runtime state written by the system and should not be treated like a stable source file
- the backend currently assumes the checkout path `/home/pi/ROV` in a few places, including the systemd unit and turret IP file lookup
- [backend/README.md](/home/pi/ROV/backend/README.md) contains additional hardware-specific usage examples
