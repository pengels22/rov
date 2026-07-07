# ROV backend config
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

DRIVE_PORT = "/dev/rov/drive"
TURRET_PORT = "/dev/rov/turret"
TURRET_SERVO_PORT = "/dev/rov/turretn"
LIDAR_PORT = "/dev/rov/lidar"

DRIVE_BAUD = 115200
TURRET_BAUD = 115200
SERVO_BAUD = 115200

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080
HTTP_MAX_BODY_BYTES = 16 * 1024

# Persistent backend logs. Each file keeps the newest LOG_MAX_LINES lines.
LOG_DIR = os.environ.get("ROV_LOG_DIR", str(PROJECT_DIR / "logs"))
LOG_MAX_LINES = 200

# Dashboard login credentials. Set both in /etc/rov-backend.env.
AUTH_USERNAME = os.environ.get("ROV_USERNAME", "").strip()
AUTH_PASSWORD = os.environ.get("ROV_PASSWORD", "")
AUTH_SESSION_HOURS = 12

# Safety supervision. The backend sends HB independently of HTTP traffic.
DRIVE_HEARTBEAT_INTERVAL_S = 1.0

# Rock 3C CSI chassis camera. The backend tries these commands in order and
# proxies the MJPEG frames to the dashboard.
CHASSIS_CAMERA_ENABLED = True
CHASSIS_CAMERA_WIDTH = 640
CHASSIS_CAMERA_HEIGHT = 480
CHASSIS_CAMERA_FPS = 15
CHASSIS_CAMERA_DEVICE = "/dev/video0"
CHASSIS_CAMERA_V4L2_CONTROLS = {}
CHASSIS_CAMERA_COMMANDS = [
    [
        "rpicam-vid",
        "-t", "0",
        "--codec", "mjpeg",
        "--width", str(CHASSIS_CAMERA_WIDTH),
        "--height", str(CHASSIS_CAMERA_HEIGHT),
        "--framerate", str(CHASSIS_CAMERA_FPS),
        "-o", "-",
    ],
    [
        "libcamera-vid",
        "-t", "0",
        "--codec", "mjpeg",
        "--width", str(CHASSIS_CAMERA_WIDTH),
        "--height", str(CHASSIS_CAMERA_HEIGHT),
        "--framerate", str(CHASSIS_CAMERA_FPS),
        "-o", "-",
    ],
    [
        "gst-launch-1.0",
        "-q",
        "v4l2src",
        f"device={CHASSIS_CAMERA_DEVICE}",
        "io-mode=4",
        "!",
        (
            "video/x-raw,"
            "format=NV12,"
            f"width={CHASSIS_CAMERA_WIDTH},"
            f"height={CHASSIS_CAMERA_HEIGHT},"
            f"framerate={CHASSIS_CAMERA_FPS}/1"
        ),
        "!",
        "videoconvert",
        "!",
        "jpegenc",
        "!",
        "fdsink",
        "fd=1",
    ],
    [
        "gst-launch-1.0",
        "-q",
        "v4l2src",
        f"device={CHASSIS_CAMERA_DEVICE}",
        "io-mode=4",
        "!",
        (
            "video/x-raw,"
            "format=UYVY,"
            f"width={CHASSIS_CAMERA_WIDTH},"
            f"height={CHASSIS_CAMERA_HEIGHT},"
            f"framerate={CHASSIS_CAMERA_FPS}/1"
        ),
        "!",
        "videoconvert",
        "!",
        "jpegenc",
        "!",
        "fdsink",
        "fd=1",
    ],
    [
        "gst-launch-1.0",
        "-q",
        "v4l2src",
        f"device={CHASSIS_CAMERA_DEVICE}",
        "io-mode=4",
        "!",
        (
            "video/x-raw,"
            "format=NV16,"
            f"width={CHASSIS_CAMERA_WIDTH},"
            f"height={CHASSIS_CAMERA_HEIGHT},"
            f"framerate={CHASSIS_CAMERA_FPS}/1"
        ),
        "!",
        "videoconvert",
        "!",
        "jpegenc",
        "!",
        "fdsink",
        "fd=1",
    ],
    [
        "gst-launch-1.0",
        "-q",
        "v4l2src",
        f"device={CHASSIS_CAMERA_DEVICE}",
        "io-mode=2",
        "!",
        (
            "video/x-raw,"
            "format=NV12,"
            f"width={CHASSIS_CAMERA_WIDTH},"
            f"height={CHASSIS_CAMERA_HEIGHT},"
            f"framerate={CHASSIS_CAMERA_FPS}/1"
        ),
        "!",
        "videoconvert",
        "!",
        "jpegenc",
        "!",
        "fdsink",
        "fd=1",
    ],
    [
        "gst-launch-1.0",
        "-q",
        "v4l2src",
        f"device={CHASSIS_CAMERA_DEVICE}",
        "io-mode=2",
        "!",
        (
            "video/x-raw,"
            "format=UYVY,"
            f"width={CHASSIS_CAMERA_WIDTH},"
            f"height={CHASSIS_CAMERA_HEIGHT},"
            f"framerate={CHASSIS_CAMERA_FPS}/1"
        ),
        "!",
        "videoconvert",
        "!",
        "jpegenc",
        "!",
        "fdsink",
        "fd=1",
    ],
    [
        "gst-launch-1.0",
        "-q",
        "v4l2src",
        f"device={CHASSIS_CAMERA_DEVICE}",
        "io-mode=2",
        "!",
        (
            "video/x-raw,"
            "format=NV16,"
            f"width={CHASSIS_CAMERA_WIDTH},"
            f"height={CHASSIS_CAMERA_HEIGHT},"
            f"framerate={CHASSIS_CAMERA_FPS}/1"
        ),
        "!",
        "videoconvert",
        "!",
        "jpegenc",
        "!",
        "fdsink",
        "fd=1",
    ],
]

# Dedicated turret controller firmware on a serial-connected Pro Micro.
# Pan is open-loop motor speed; tilt is an absolute servo angle:
#   P-30
#   T45
#   PT-30,45
#   ?
PAN_MIN_SPEED = -100
PAN_MAX_SPEED = 100
TILT_MIN_ANGLE = 0
TILT_MAX_ANGLE = 180
SERVO_MIN_POS = TILT_MIN_ANGLE
SERVO_MAX_POS = TILT_MAX_ANGLE
SERVO_CENTER_POS = 90

PAN_SERVO_ID = 1
TILT_SERVO_ID = 2

# Pi-side relay outputs for power control.
# These use libgpiod chip names and line offsets, not BCM pin numbers.
# K1 -> BCM17 -> header PIN_11 -> gpiochip3 line 1
# K2 transfer pair -> BCM27 -> header PIN_13 -> gpiochip3 line 2
# Both physical transfer relays share this GPIO:
#   GPIO HIGH: battery isolated, shore power enabled
#   GPIO LOW: shore power isolated, battery enabled
# Set active_high to False if the relay module energizes on GPIO low.
MOTOR_ENABLE_RELAY_GPIO_CHIP = "gpiochip3"
MOTOR_ENABLE_RELAY_GPIO_LINE = 1
MOTOR_ENABLE_RELAY_ACTIVE_HIGH = False

POWER_SOURCE_RELAY_GPIO_CHIP = "gpiochip3"
POWER_SOURCE_RELAY_GPIO_LINE = 2
POWER_SOURCE_RELAY_ACTIVE_HIGH = True
