# ROV API Endpoints

This file lists the HTTP endpoints currently implemented in this repo, based on the active route registration and handler code.

## Pi Backend

Implemented in `backend/app.py`.

### GET

- `/`
- `/api`
- `/api/status`
- `/api/dashboard`
- `/api/drive/status`
- `/api/turret/status`
- `/api/turret/telemetry`
- `/api/turret/camera/stream`
- `/api/servo/status`
- `/api/power/status`
- `/api/lidar`
- `/api/chassis/camera/status`
- `/api/chassis/camera/stream`

### POST

- `/api/drive/stop`
- `/api/drive/joy`
- `/api/drive/move`
- `/api/drive/stream`
- `/api/drive/reset_encoders`
- `/api/drive/leds`
- `/api/turret/accel_reinit`
- `/api/turret/cam_reinit`
- `/api/turret/camera/brightness`
- `/api/turret/tilt_zero`
- `/api/turret/aim`
- `/api/turret/home`
- `/api/turret/mark_pan_home`
- `/api/power/motor_enable`
- `/api/power/battery_kill`
- `/api/servo/move`
- `/api/servo/center`

### OPTIONS

- Generic CORS preflight handler for backend routes

## Turret ESP32 HTTP

Implemented in `firmware/turret_xiao/turret_xiao.ino`.

### Port 80

- `GET /`
- `GET /status`

### Port 81

- MJPEG stream server

Notes:

- The Pi backend proxies the turret stream at `/api/turret/camera/stream`.
- The proxy connects upstream to `http://<turret-ip>:81/stream`, avoiding
  browser cross-origin and direct-network issues.
- The ESP32 camera stream is served by a raw `WiFiServer` on port `81`, not a path-based router.
- In practice, requests to `http://<turret-ip>:81` and `http://<turret-ip>:81/stream` both target the same stream listener.
- The Rock 3C CSI chassis camera is proxied by the Pi backend at `/api/chassis/camera/stream`.
