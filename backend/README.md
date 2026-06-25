# ROV Backend Starter

This is a small no-venv, stdlib HTTP API backend for the Radxa ROV controller.

It talks to:

- `/dev/rov/drive` — drive board running `drive_nano.ino`
- `/dev/rov/turret` — turret XIAO running `turret_xiao.ino`
- `/dev/rov/turretn` — turret servo controller running `turret_servos.ino`
- `/dev/rov/lidar` — LiDAR UART

Only external Python dependency is `pyserial`, which is already installed on the ROV.
The chassis CSI camera stream uses `rpicam-vid` or `libcamera-vid` when present.

Pi-side ultrasonics use the system `gpiomon`/`gpioset` tools if you configure
their chip/line values in [config.py](/home/pi/ROV/backend/config.py:11).

## Install on the Radxa

Copy the `backend` folder to:

```bash
/home/pi/ROV/backend
```

Run:

```bash
cd ~/ROV/backend
python3 app.py --host 0.0.0.0 --port 8080
```

Test:

```bash
curl http://ROV.local:8080/api/status
curl http://ROV.local:8080/api/drive/status
curl http://ROV.local:8080/api/turret/telemetry
curl http://ROV.local:8080/api/servo/status
```

## Safe servo test

The backend now talks to the dedicated turret servo controller over serial.
That firmware accepts direct servo angles in the `0..180` range.

First physical servo test:

```bash
curl -X POST http://ROV.local:8080/api/servo/move \
  -H 'Content-Type: application/json' \
  -d '{"id":1,"angle":90,"time_ms":1000}'
```

Then tiny move:

```bash
curl -X POST http://ROV.local:8080/api/servo/move \
  -H 'Content-Type: application/json' \
  -d '{"id":1,"angle":100,"time_ms":1000}'
```

Return center-ish:

```bash
curl -X POST http://ROV.local:8080/api/servo/move \
  -H 'Content-Type: application/json' \
  -d '{"id":1,"angle":90,"time_ms":1000}'
```

## Drive examples

Joystick command:

```bash
curl -X POST http://ROV.local:8080/api/drive/joy \
  -H 'Content-Type: application/json' \
  -d '{"throttle":100,"turn":0}'
```

Stop:

```bash
curl -X POST http://ROV.local:8080/api/drive/stop
```

Distance move:

```bash
curl -X POST http://ROV.local:8080/api/drive/move \
  -H 'Content-Type: application/json' \
  -d '{"dir":"fwd","dist_in":12,"speed":120}'

Set the drive LEDs to healthy green, or restore automatic drive lighting:

```bash
curl -X POST http://ROV.local:8080/api/drive/leds \
  -H 'Content-Type: application/json' \
  -d '{"mode":"green"}'

curl -X POST http://ROV.local:8080/api/drive/leds \
  -H 'Content-Type: application/json' \
  -d '{"mode":"auto"}'
```
```

## Turret examples

```bash
curl http://ROV.local:8080/api/turret/status
curl http://ROV.local:8080/api/turret/telemetry
```

Mark pan home after the physical switch/homing process later:

```bash
curl -X POST http://ROV.local:8080/api/turret/mark_pan_home \
  -H 'Content-Type: application/json' \
  -d '{"position":90}'
```

Mark tilt zero after accelerometer level calibration:

```bash
curl -X POST http://ROV.local:8080/api/turret/tilt_zero \
  -H 'Content-Type: application/json' \
  -d '{"position":90}'
```
