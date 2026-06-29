# ROV backend config

DRIVE_PORT = "/dev/rov/drive"
TURRET_PORT = "/dev/rov/turret"
TURRET_SERVO_PORT = "/dev/rov/turretn"
LIDAR_PORT = "/dev/rov/lidar"

DRIVE_BAUD = 115200
TURRET_BAUD = 115200
SERVO_BAUD = 115200

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080

# Rock 3C CSI chassis camera. The backend tries these commands in order and
# proxies the MJPEG frames to the dashboard.
CHASSIS_CAMERA_ENABLED = True
CHASSIS_CAMERA_WIDTH = 800
CHASSIS_CAMERA_HEIGHT = 600
CHASSIS_CAMERA_FPS = 15
CHASSIS_CAMERA_DEVICE = "/dev/video0"
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

# Pi-side ultrasonic sensors.
# Leave any line as None until the wiring is confirmed.
# These use libgpiod chip names and line offsets, not BCM pin numbers.
ULTRASONIC_REFRESH_MS = 250
ULTRASONIC_ECHO_TIMEOUT_MS = 45
ULTRASONIC_FRONT_TRIGGER_CHIP = "gpiochip3"  # Pi BCM23 -> header PIN_16
ULTRASONIC_FRONT_TRIGGER_LINE = 9
ULTRASONIC_FRONT_ECHO_CHIP = "gpiochip3"     # Pi BCM24 -> header PIN_18
ULTRASONIC_FRONT_ECHO_LINE = 10
ULTRASONIC_REAR_TRIGGER_CHIP = "gpiochip3"   # Pi BCM25 -> header PIN_22
ULTRASONIC_REAR_TRIGGER_LINE = 17
ULTRASONIC_REAR_ECHO_CHIP = "gpiochip4"      # Pi BCM8 -> header PIN_24
ULTRASONIC_REAR_ECHO_LINE = 22

# Dedicated hobby-servo controller firmware on a serial-connected Pro Micro.
# The firmware accepts absolute angles in degrees:
#   P90
#   T45
#   PT90,45
#   ?
SERVO_MIN_POS = 0
SERVO_MAX_POS = 270
SERVO_CENTER_POS = 90

PAN_SERVO_ID = 1
TILT_SERVO_ID = 2

# Pi-side relay outputs for power control.
# These use libgpiod chip names and line offsets, not BCM pin numbers.
# K1 -> BCM17 -> header PIN_11 -> gpiochip3 line 1
# K2 -> BCM27 -> header PIN_13 -> gpiochip3 line 2
# Set active_high to False if the relay module energizes on GPIO low.
MOTOR_ENABLE_RELAY_GPIO_CHIP = "gpiochip3"
MOTOR_ENABLE_RELAY_GPIO_LINE = 1
MOTOR_ENABLE_RELAY_ACTIVE_HIGH = False

BATTERY_KILL_RELAY_GPIO_CHIP = "gpiochip3"
BATTERY_KILL_RELAY_GPIO_LINE = 2
BATTERY_KILL_RELAY_ACTIVE_HIGH = False
