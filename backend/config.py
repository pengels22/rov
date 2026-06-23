# ROV backend config

DRIVE_PORT = "/dev/serial/by-id/usb-Arduino_Nano_ESP32_4827E2FC9A24-if01"
TURRET_PORT = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_8C:BF:EA:8F:4C:18-if00"
SERVO_PORT = "/dev/serial/by-id/usb-Arduino_LLC_Arduino_Leonardo-if00"

DRIVE_BAUD = 115200
TURRET_BAUD = 115200
SERVO_BAUD = 115200

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080

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
SERVO_MAX_POS = 180
SERVO_CENTER_POS = 90

PAN_SERVO_ID = 1
TILT_SERVO_ID = 2

# Pi-side relay outputs for power control.
# These use libgpiod chip names and line offsets, not BCM pin numbers.
# K1 -> BCM17 -> header PIN_11 -> gpiochip3 line 1
# K2 -> BCM27 -> header PIN_13 -> gpiochip3 line 2
# Set active_high to False if the relay module energizes on GPIO low.
MOTOR_ENABLE_RELAY_GPIO_CHIP = "gpiochip3"
MOTOR_ENABLE_RELAY_GPIO_LINE = 2
MOTOR_ENABLE_RELAY_ACTIVE_HIGH = False

BATTERY_KILL_RELAY_GPIO_CHIP = "gpiochip3"
BATTERY_KILL_RELAY_GPIO_LINE = 1
BATTERY_KILL_RELAY_ACTIVE_HIGH = False
