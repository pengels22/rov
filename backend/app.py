#!/usr/bin/env python3
import argparse
import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import (
    DRIVE_PORT, TURRET_PORT,
    DRIVE_BAUD, TURRET_BAUD,
    HTTP_HOST, HTTP_PORT,
    PAN_SERVO_ID, TILT_SERVO_ID,
    SERVO_MIN_POS, SERVO_MAX_POS,
    SERVO_CENTER_POS, TURRET_SERVO_PORT, SERVO_BAUD,
)
from serial_line import SerialLine
from drive import DriveController
from lidar_view import LidarService
from turret import TurretController
from servo_backend import ServoController
from relay_output import RelayController
from turret_state import TurretState
from ultrasonic import PiUltrasonicService
from chassis_camera import ChassisCameraService

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
TEMPLATES_DIR = PROJECT_DIR / "Templates"
INDEX_TEMPLATE = TEMPLATES_DIR / "index.html"

drive = DriveController(SerialLine(DRIVE_PORT, DRIVE_BAUD, name="drive"))
turret = TurretController(SerialLine(TURRET_PORT, TURRET_BAUD, name="turret"))
servos = ServoController()
relays = RelayController()
turret_state = TurretState()
lidar = LidarService()
ultrasonic = PiUltrasonicService()
chassis_camera = ChassisCameraService()


def ok(data=None, status=200):
    return status, data if data is not None else {"ok": True}


def err(message, status=400, extra=None):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return status, payload


def read_json(handler):
    n = int(handler.headers.get("Content-Length", "0") or "0")
    if n <= 0:
        return {}
    raw = handler.rfile.read(n).decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def system_status():
    # Attempt to gather fresh data from devices. If a device call fails,
    # fall back to the last cached values so the endpoint stays responsive.
    devices = {
        "drive": drive.line.status(),
        "turret": turret.line.status(),
        "servo": servos.device_status(),
        "relays": relays.snapshot(),
    }

    # Gather latest device state with safe fallbacks
    try:
        drive_status = drive.status()
    except Exception:
        drive_status = drive.last_status

    try:
        turret_status = turret.status()
    except Exception:
        turret_status = turret.last_status

    try:
        turret_telemetry = turret.telemetry()
    except Exception:
        turret_telemetry = turret.last_telemetry

    try:
        servo_status = servos.status()
        pan_status = servo_status.get("pan", {})
        tilt_status = servo_status.get("tilt", {})
        if "position" in pan_status:
            turret_state.update_pan_position(pan_status["position"])
        if "position" in tilt_status:
            turret_state.update_tilt_position(tilt_status["position"])
    except Exception:
        servo_status = {"raw": servos.last_command}

    # Read any persisted turret IP saved by the serial monitor helper
    saved_turret_ip = None
    try:
        with open('/home/pi/ROV/config/turret_ip.txt', 'r') as f:
            saved_turret_ip = f.read().strip()
    except Exception:
        saved_turret_ip = None

    turret_stream_url = None
    if saved_turret_ip:
        turret_stream_url = f"http://{saved_turret_ip}:81/stream"

    out = {
        "ok": True,
        "devices": devices,
        "drive_status": drive_status,
        "turret_status": turret_status,
        "turret_telemetry": turret_telemetry,
        "servo_status": servo_status,
        "relay_status": relays.snapshot(),
        "turret_state": turret_state.as_dict(),
        "saved_turret_ip": saved_turret_ip,
        "turret_stream_url": turret_stream_url,
        "turret_camera": {
            "brightness": turret.camera_brightness,
        },
        "chassis_camera": chassis_camera.status(),
        "pi_ultrasonic": ultrasonic.snapshot(),
        "lidar": lidar.snapshot(),
        "last": {
            "drive": drive.last_status,
            "turret_status": turret.last_status,
            "turret_telemetry": turret.last_telemetry,
            "servo": servos.last_command,
        },
        "endpoints": [
            "GET /api/status",
            "GET /api/drive/status",
            "POST /api/drive/joy",
            "POST /api/drive/move",
            "POST /api/drive/stop",
            "POST /api/drive/stream",
            "POST /api/drive/leds",
            "POST /api/drive/reset_encoders",
            "GET /api/turret/status",
            "GET /api/turret/telemetry",
            "POST /api/turret/accel_reinit",
            "POST /api/turret/cam_reinit",
            "POST /api/turret/camera/brightness",
            "POST /api/turret/tilt_zero",
            "POST /api/turret/aim",
            "GET /api/power/status",
            "POST /api/power/motor_enable",
            "POST /api/power/battery_kill",
            "GET /api/servo/status",
            "POST /api/servo/move",
            "POST /api/servo/center",
            "POST /api/turret/mark_pan_home",
            "GET /api/chassis/camera/status",
            "GET /api/chassis/camera/stream",
        ],
    }
    return out


def dashboard_status():
    status = system_status()
    status.pop("lidar", None)
    return status


def move_turret_relative(body):
    pan_delta = int(body.get("pan_delta", 0))
    tilt_delta = int(body.get("tilt_delta", 0))

    pan_delta = max(-12, min(12, pan_delta))
    tilt_delta = max(-12, min(12, tilt_delta))
    if tilt_delta != 0:
        tilt_delta = max(-3, min(3, int(round(tilt_delta * 0.25))))
        if tilt_delta == 0:
            tilt_delta = 1 if int(body.get("tilt_delta", 0)) > 0 else -1

    time_ms = max(120, min(1000, int(body.get("time_ms", 180))))

    next_pan = max(SERVO_MIN_POS, min(SERVO_MAX_POS, turret_state.pan_pos + pan_delta))
    next_tilt = max(SERVO_MIN_POS, min(SERVO_MAX_POS, turret_state.tilt_pos + tilt_delta))
    results = {}

    move_pan = next_pan != turret_state.pan_pos
    move_tilt = next_tilt != turret_state.tilt_pos

    if move_pan and move_tilt:
        both_result = servos.move_both(next_pan, next_tilt, time_ms)
        turret_state.update_pan_position(next_pan)
        turret_state.update_tilt_position(next_tilt)
        results["pan"] = {
            "servo_id": turret_state.pan_servo_id,
            "position": next_pan,
            "angle": next_pan,
            "time_ms": time_ms,
        }
        results["tilt"] = {
            "servo_id": turret_state.tilt_servo_id,
            "position": next_tilt,
            "angle": next_tilt,
            "time_ms": time_ms,
        }
        results["combined"] = both_result
    else:
        if move_pan:
            pan_result = servos.move(turret_state.pan_servo_id, next_pan, time_ms)
            turret_state.update_pan_position(pan_result["position"])
            results["pan"] = pan_result

        if move_tilt:
            tilt_result = servos.move(turret_state.tilt_servo_id, next_tilt, time_ms)
            turret_state.update_tilt_position(tilt_result["position"])
            results["tilt"] = tilt_result

    return {
        "servo": results,
        "turret_state": turret_state.as_dict(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ROVBackend/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def _send_json(self, status, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status, html):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path == "/api/chassis/camera/stream":
                self._send_chassis_camera_stream()
                return

            status, payload, content_type = self.route_get(path)
            if content_type == "html":
                self._send_html(status, payload)
            else:
                self._send_json(status, payload)
        except Exception as e:
            self._send_json(500, {
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc(),
            })

    def _send_chassis_camera_stream(self):
        if not chassis_camera.enabled:
            self._send_json(503, {"ok": False, "error": "chassis camera disabled"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        last_frame_id = 0
        while True:
            frame, frame_id = chassis_camera.wait_for_frame(last_frame_id)
            if not frame or frame_id == last_frame_id:
                continue

            last_frame_id = frame_id
            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            body = read_json(self)
            status, payload = self.route_post(path, body)
            self._send_json(status, payload)
        except Exception as e:
            self._send_json(500, {
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc(),
            })

    def route_get(self, path):
        if path == "/":
            if INDEX_TEMPLATE.exists():
                return 200, INDEX_TEMPLATE.read_text(encoding="utf-8"), "html"
            return 404, "<h1>Templates/index.html not found</h1>", "html"

        if path in ("/api", "/api/status"):
            status, payload = ok(system_status())
            return status, payload, "json"

        if path == "/api/dashboard":
            status, payload = ok(dashboard_status())
            return status, payload, "json"

        if path == "/api/drive/status":
            status, payload = ok(drive.status())
            return status, payload, "json"

        if path == "/api/turret/status":
            status, payload = ok(turret.status())
            return status, payload, "json"

        if path == "/api/turret/telemetry":
            telemetry = turret.telemetry()
            payload = {
                **telemetry,
                "turret_state": turret_state.as_dict(),
            }
            status, wrapped = ok(payload)
            return status, wrapped, "json"

        if path == "/api/servo/status":
            servo_status = servos.status()
            pan_status = servo_status.get("pan", {})
            tilt_status = servo_status.get("tilt", {})
            if "position" in pan_status:
                turret_state.update_pan_position(pan_status["position"])
            if "position" in tilt_status:
                turret_state.update_tilt_position(tilt_status["position"])
            status, payload = ok(servo_status)
            return status, payload, "json"

        if path == "/api/power/status":
            status, payload = ok(relays.snapshot())
            return status, payload, "json"

        if path == "/api/lidar":
            status, payload = ok(lidar.snapshot())
            return status, payload, "json"

        if path == "/api/chassis/camera/status":
            status, payload = ok(chassis_camera.status())
            return status, payload, "json"

        status, payload = err("not found", 404)
        return status, payload, "json"

    def route_post(self, path, body):
        if path == "/api/drive/stop":
            return ok({"response": drive.stop()})

        if path == "/api/drive/joy":
            return ok({
                "response": drive.joy(
                    body.get("throttle", 0),
                    body.get("turn", 0),
                )
            })

        if path == "/api/drive/move":
            return ok(drive.move(
                body.get("dir", "fwd"),
                body.get("dist_in", 0),
                body.get("speed", 120),
            ))

        if path == "/api/drive/stream":
            return ok({"response": drive.stream(body.get("interval_ms", 0))})

        if path == "/api/drive/reset_encoders":
            return ok({"response": drive.reset_encoders()})

        if path == "/api/drive/leds":
            return ok({"response": drive.set_led_mode(body.get("mode", "auto"))})

        if path == "/api/turret/accel_reinit":
            return ok({"response": turret.accel_reinit()})

        if path == "/api/turret/cam_reinit":
            return ok({"response": turret.cam_reinit()})

        if path == "/api/turret/camera/brightness":
            level = int(body.get("brightness", 0))
            return ok({
                "response": turret.set_camera_brightness(level),
                "turret_camera": {
                    "brightness": turret.camera_brightness,
                },
            })

        if path == "/api/turret/tilt_zero":
            resp = turret.tilt_zero()
            turret_state.set_tilt_zero(body.get("angle", body.get("position")))
            return ok({"response": resp, "turret_state": turret_state.as_dict()})

        if path == "/api/turret/aim":
            return ok(move_turret_relative(body))

        if path == "/api/turret/mark_pan_home":
            turret_state.set_pan_home(body.get("angle", body.get("position")))
            return ok({"turret_state": turret_state.as_dict()})

        if path == "/api/power/motor_enable":
            enabled = bool(body.get("enabled", True))
            return ok({
                "relay": relays.set_state("motor_enable", enabled),
                "relay_status": relays.snapshot(),
            })

        if path == "/api/power/battery_kill":
            enabled = bool(body.get("enabled", True))
            return ok({
                "relay": relays.set_state("battery_kill", enabled),
                "relay_status": relays.snapshot(),
            })

        if path == "/api/servo/move":
            servo_id = int(body.get("id", PAN_SERVO_ID))
            position = int(body.get("position", SERVO_CENTER_POS))
            if "angle" in body:
                position = int(body.get("angle"))
            time_ms = int(body.get("time_ms", 1000))
            result = servos.move(servo_id, position, time_ms)

            if servo_id == turret_state.pan_servo_id:
                turret_state.update_pan_position(result["position"])
            elif servo_id == turret_state.tilt_servo_id:
                turret_state.update_tilt_position(result["position"])

            return ok({"servo": result, "turret_state": turret_state.as_dict()})

        if path == "/api/servo/center":
            servo_id = int(body.get("id", PAN_SERVO_ID))
            time_ms = int(body.get("time_ms", 1000))
            result = servos.center(servo_id, time_ms)

            if servo_id == turret_state.pan_servo_id:
                turret_state.update_pan_position(result["position"])
            elif servo_id == turret_state.tilt_servo_id:
                turret_state.update_tilt_position(result["position"])

            return ok({"servo": result, "turret_state": turret_state.as_dict()})

        return err("not found", 404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=HTTP_HOST)
    parser.add_argument("--port", type=int, default=HTTP_PORT)
    args = parser.parse_args()

    lidar.start()
    chassis_camera.start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ROV backend listening on http://{args.host}:{args.port}")
    print("Using devices:")
    print(f"  drive : {DRIVE_PORT} @ {DRIVE_BAUD}")
    print(f"  turret: {TURRET_PORT} @ {TURRET_BAUD}")
    print(f"  servo : {TURRET_SERVO_PORT} @ {SERVO_BAUD}")
    print(f"  lidar : {lidar.port} @ {lidar.baud}")
    print(f"  chassis camera: {chassis_camera.status()['stream_url']}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
