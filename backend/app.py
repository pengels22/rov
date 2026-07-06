#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from config import (
    DRIVE_PORT, TURRET_PORT,
    DRIVE_BAUD, TURRET_BAUD,
    HTTP_HOST, HTTP_PORT,
    AUTH_PASSWORD, AUTH_SESSION_HOURS, AUTH_USERNAME,
    CLIENT_LEASE_TIMEOUT_S, DRIVE_HEARTBEAT_INTERVAL_S,
    HTTP_MAX_BODY_BYTES,
    PAN_SERVO_ID, TILT_SERVO_ID,
    SERVO_MIN_POS, SERVO_MAX_POS,
    SERVO_CENTER_POS, TURRET_SERVO_PORT, SERVO_BAUD,
)
from serial_line import SerialLine
from drive import DriveController, DriveDeviceError, DriveSafetySupervisor
from lidar_view import LidarService
from turret import TurretController
from servo_backend import ServoController
from relay_output import RelayController
from turret_state import TurretState
from ultrasonic import PiUltrasonicService
from chassis_camera import ChassisCameraService
from validation import require_bool
from auth import SessionAuth

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
TEMPLATES_DIR = PROJECT_DIR / "Templates"
INDEX_TEMPLATE = TEMPLATES_DIR / "index.html"
LOGIN_TEMPLATE = TEMPLATES_DIR / "login.html"
TURRET_IP_FILE = PROJECT_DIR / "config" / "turret_ip.txt"

drive = DriveController(SerialLine(DRIVE_PORT, DRIVE_BAUD, name="drive"))
turret = TurretController(SerialLine(TURRET_PORT, TURRET_BAUD, name="turret"))
servos = ServoController()
relays = RelayController()


def disable_motion_outputs():
    try:
        servos.stop_pan()
    except Exception:
        pass
    relays.disable_motor()


drive_safety = DriveSafetySupervisor(
    drive,
    disable_motion_outputs,
    heartbeat_interval_s=DRIVE_HEARTBEAT_INTERVAL_S,
    client_timeout_s=CLIENT_LEASE_TIMEOUT_S,
)
turret_state = TurretState()
lidar = LidarService()
ultrasonic = PiUltrasonicService()
chassis_camera = ChassisCameraService()
session_auth = SessionAuth(
    AUTH_USERNAME,
    AUTH_PASSWORD,
    session_hours=AUTH_SESSION_HOURS,
)


def ok(data=None, status=200):
    return status, data if data is not None else {"ok": True}


def err(message, status=400, extra=None):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return status, payload


def read_json(handler):
    try:
        n = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError as exc:
        raise ValueError("invalid Content-Length") from exc
    if n <= 0:
        return {}
    if n > HTTP_MAX_BODY_BYTES:
        raise RequestTooLarge(
            f"request body exceeds {HTTP_MAX_BODY_BYTES} bytes"
        )
    raw = handler.rfile.read(n).decode("utf-8", errors="replace")
    try:
        value = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ValueError("request body is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("request JSON must be an object")
    return value


class RequestTooLarge(ValueError):
    pass


def load_saved_turret_ip():
    try:
        value = TURRET_IP_FILE.read_text(encoding="utf-8").strip()
        return value or None
    except Exception:
        return None


def save_turret_ip(ip):
    ip = (ip or "").strip()
    if not ip:
        return
    try:
        TURRET_IP_FILE.parent.mkdir(parents=True, exist_ok=True)
        TURRET_IP_FILE.write_text(ip + "\n", encoding="utf-8")
    except Exception:
        pass


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

    live_turret_ip = turret_status.get("ip") if isinstance(turret_status, dict) else None
    if live_turret_ip:
        save_turret_ip(live_turret_ip)

    try:
        turret_telemetry = turret.telemetry()
    except Exception:
        turret_telemetry = turret.last_telemetry

    try:
        servo_status = servos.status()
        pan_status = servo_status.get("pan", {})
        tilt_status = servo_status.get("tilt", {})
        if "speed" in pan_status:
            turret_state.update_pan_status(
                pan_status["speed"],
                pan_status.get("homed", False),
                pan_status.get("home_switch_pressed", False),
            )
        if "position" in tilt_status:
            turret_state.update_tilt_position(tilt_status["position"])
    except Exception:
        servo_status = {
            **servos.last_status,
            "stale": True,
            "last_error": servos.last_error,
        }

    # Read the persisted turret IP used by the dashboard stream URL.
    saved_turret_ip = load_saved_turret_ip()

    turret_stream_url = None
    if saved_turret_ip:
        turret_stream_url = "/api/turret/camera/stream"

    out = {
        "ok": True,
        "devices": devices,
        "drive_status": drive_status,
        "turret_status": turret_status,
        "turret_telemetry": turret_telemetry,
        "servo_status": servo_status,
        "servo_battery_v": servo_status.get("battery_v"),
        "relay_status": relays.snapshot(),
        "drive_safety": drive_safety.snapshot(),
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
            "GET /api/turret/camera/stream",
            "POST /api/turret/accel_reinit",
            "POST /api/turret/cam_reinit",
            "POST /api/turret/camera/brightness",
            "POST /api/turret/tilt_zero",
            "POST /api/turret/aim",
            "POST /api/turret/stop",
            "POST /api/turret/home",
            "POST /api/turret/set_home",
            "GET /api/power/status",
            "POST /api/power/motor_enable",
            "POST /api/power/shore_power",
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

    pan_speed = int(round((pan_delta / 12.0) * 100.0))
    next_tilt = max(SERVO_MIN_POS, min(SERVO_MAX_POS, turret_state.tilt_pos + tilt_delta))
    results = {}

    move_pan = pan_speed != turret_state.pan_speed
    move_tilt = next_tilt != turret_state.tilt_pos

    if move_pan and move_tilt:
        both_result = servos.set_pan_and_tilt(pan_speed, next_tilt)
        turret_state.pan_speed = both_result["pan_speed"]
        turret_state.update_tilt_position(next_tilt)
        results["combined"] = both_result
    else:
        if move_pan:
            pan_result = servos.set_pan_speed(pan_speed)
            turret_state.pan_speed = pan_result["speed"]
            results["pan"] = pan_result

        if move_tilt:
            tilt_result = servos.set_tilt_angle(next_tilt)
            turret_state.update_tilt_position(tilt_result["position"])
            results["tilt"] = tilt_result

    return {
        "servo": results,
        "turret_state": turret_state.as_dict(),
    }


def move_turret_home(body):
    target_tilt = int(turret_state.tilt_zero_angle)
    pan_result = servos.home_pan()
    tilt_result = servos.set_tilt_angle(target_tilt)
    turret_state.update_pan_status(0, True, True)
    turret_state.update_tilt_position(target_tilt)
    return {
        "servo": {"pan": pan_result, "tilt": tilt_result},
        "turret_state": turret_state.as_dict(),
    }

def set_turret_home():
    servo_status = servos.status()
    tilt_position = servo_status["tilt"]["position"]
    pan_result = servos.home_pan()
    turret_state.update_pan_status(0, True, True)
    turret_state.set_tilt_zero(tilt_position)
    return {
        "home": {
            "pan": pan_result,
            "tilt_position": tilt_position,
        },
        "turret_state": turret_state.as_dict(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ROVBackend/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def _send_json(self, status, payload, headers=None):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status, html):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self._send_json(405, {"ok": False, "error": "cross-origin requests are disabled"})

    def _authorized(self, path):
        # Safe disconnect must remain usable by sendBeacon during page teardown.
        if path == "/api/safety/disconnect":
            return True

        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host:
            parsed = urlparse(origin)
            if parsed.netloc != host:
                return False

        if session_auth.authenticated(self.headers.get("Cookie", "")):
            return True
        return not session_auth.configured

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path == "/login":
                if session_auth.authenticated(self.headers.get("Cookie", "")):
                    self._redirect("/")
                elif LOGIN_TEMPLATE.exists():
                    self._send_html(200, LOGIN_TEMPLATE.read_text(encoding="utf-8"))
                else:
                    self._send_html(500, "<h1>Login template missing</h1>")
                return
            if not self._authorized(path):
                if path == "/":
                    self._redirect("/login")
                else:
                    self._send_json(401, {"ok": False, "error": "login required"})
                return
            if path == "/api/chassis/camera/stream":
                self._send_chassis_camera_stream()
                return
            if path == "/api/turret/camera/stream":
                self._send_turret_camera_stream()
                return

            status, payload, content_type = self.route_get(path)
            if content_type == "html":
                self._send_html(status, payload)
            else:
                self._send_json(status, payload)
        except (BrokenPipeError, ConnectionResetError):
            return
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception:
            self._send_json(500, {"ok": False, "error": "internal server error"})

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

    def _send_turret_camera_stream(self):
        turret_ip = load_saved_turret_ip()
        if not turret_ip:
            self._send_json(503, {"ok": False, "error": "turret IP unavailable"})
            return

        request = Request(
            f"http://{turret_ip}:81/stream",
            headers={"User-Agent": "ROV-Backend/0.1"},
        )
        try:
            upstream = urlopen(request, timeout=5.0)
        except Exception as exc:
            self._send_json(502, {
                "ok": False,
                "error": f"turret camera unavailable: {exc}",
            })
            return

        with upstream:
            content_type = upstream.headers.get(
                "Content-Type",
                "multipart/x-mixed-replace; boundary=frame",
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            while True:
                try:
                    chunk = upstream.read(16 * 1024)
                    if not chunk:
                        return
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    return

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            try:
                body = read_json(self)
                session_id = session_auth.login(
                    body.get("username", ""),
                    body.get("password", ""),
                )
                if not session_id:
                    self._send_json(401, {
                        "ok": False,
                        "error": "invalid username or password",
                    })
                    return
                self._send_json(
                    200,
                    {"ok": True},
                    {"Set-Cookie": session_auth.session_cookie(session_id)},
                )
            except (ValueError, RequestTooLarge) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/logout":
            session_auth.logout(self.headers.get("Cookie", ""))
            drive_safety.disconnect_client()
            self._send_json(
                200,
                {"ok": True},
                {"Set-Cookie": session_auth.clear_cookie()},
            )
            return

        if not self._authorized(path):
            self._send_json(401, {"ok": False, "error": "login required"})
            return
        try:
            body = read_json(self)
            status, payload = self.route_post(path, body)
            self._send_json(status, payload)
        except RequestTooLarge as exc:
            self._send_json(413, {"ok": False, "error": str(exc)})
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except DriveDeviceError as exc:
            self._send_json(409, {
                "ok": False,
                "error": str(exc),
                "device_response": exc.response,
            })
        except (BrokenPipeError, ConnectionResetError):
            drive_safety.safe_shutdown("control client connection lost")
        except Exception:
            self._send_json(500, {"ok": False, "error": "internal server error"})

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
            if "speed" in pan_status:
                turret_state.update_pan_status(
                    pan_status["speed"],
                    pan_status.get("homed", False),
                    pan_status.get("home_switch_pressed", False),
                )
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
        if path == "/api/client/heartbeat":
            drive_safety.renew_client_lease()
            return ok({"drive_safety": drive_safety.snapshot()})

        if path == "/api/safety/disconnect":
            drive_safety.disconnect_client()
            return ok({"drive_safety": drive_safety.snapshot()})

        if path == "/api/drive/stop":
            return ok({"response": drive.stop()})

        if path == "/api/drive/joy":
            drive_safety.renew_client_lease()
            return ok({
                "response": drive.joy(
                    body.get("throttle", 0),
                    body.get("turn", 0),
                )
            })

        if path == "/api/drive/move":
            drive_safety.renew_client_lease()
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

        if path == "/api/turret/stop":
            result = servos.stop_pan()
            turret_state.pan_speed = 0
            return ok({"servo": result, "turret_state": turret_state.as_dict()})

        if path == "/api/turret/home":
            return ok(move_turret_home(body))

        if path == "/api/turret/set_home":
            return ok(set_turret_home())

        if path == "/api/turret/mark_pan_home":
            result = servos.home_pan()
            turret_state.update_pan_status(0, True, True)
            return ok({"servo": result, "turret_state": turret_state.as_dict()})

        if path == "/api/power/motor_enable":
            enabled = require_bool(body, "enabled")
            if enabled:
                drive_safety.renew_client_lease()
            return ok({
                "relay": relays.set_state("motor_enable", enabled),
                "relay_status": relays.snapshot(),
            })

        if path in ("/api/power/shore_power", "/api/power/battery_kill"):
            enabled = require_bool(body, "enabled")
            return ok({
                "relay": relays.set_state("power_source", enabled),
                "relay_status": relays.snapshot(),
            })

        if path == "/api/servo/move":
            servo_id = int(body.get("id", PAN_SERVO_ID))
            if servo_id == turret_state.pan_servo_id:
                if "speed" in body:
                    value = int(body["speed"])
                elif "position" in body:
                    value = int(body["position"])
                else:
                    raise ValueError("pan move requires speed")
                result = servos.set_pan_speed(value)
                turret_state.pan_speed = result["speed"]
            elif servo_id == turret_state.tilt_servo_id:
                if "angle" in body:
                    value = int(body["angle"])
                elif "position" in body:
                    value = int(body["position"])
                else:
                    raise ValueError("tilt move requires angle")
                result = servos.set_tilt_angle(value)
                turret_state.update_tilt_position(result["position"])
            else:
                raise ValueError(f"unknown servo id: {servo_id}")

            return ok({"servo": result, "turret_state": turret_state.as_dict()})

        if path == "/api/servo/center":
            servo_id = int(body.get("id", PAN_SERVO_ID))
            time_ms = int(body.get("time_ms", 1000))
            result = servos.center(servo_id, time_ms)

            if servo_id == turret_state.pan_servo_id:
                turret_state.pan_speed = result["speed"]
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
    drive_safety.start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ROV backend listening on http://{args.host}:{args.port}")
    print("Using devices:")
    print(f"  drive : {DRIVE_PORT} @ {DRIVE_BAUD}")
    print(f"  turret: {TURRET_PORT} @ {TURRET_BAUD}")
    print(f"  servo : {TURRET_SERVO_PORT} @ {SERVO_BAUD}")
    print(f"  lidar : {lidar.port} @ {lidar.baud}")
    print(f"  chassis camera: {chassis_camera.status()['stream_url']}")
    if not session_auth.configured:
        print("WARNING: authentication is disabled; set ROV_USERNAME and ROV_PASSWORD")
    try:
        httpd.serve_forever()
    finally:
        drive_safety.safe_shutdown("backend shutting down")
        drive_safety.stop()
        lidar.stop()
        chassis_camera.stop()


if __name__ == "__main__":
    main()
