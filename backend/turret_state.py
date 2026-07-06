from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from config import PAN_SERVO_ID, TILT_SERVO_ID, SERVO_CENTER_POS


@dataclass
class TurretState:
    pan_servo_id: int = PAN_SERVO_ID
    tilt_servo_id: int = TILT_SERVO_ID

    pan_homed: bool = False
    tilt_zeroed: bool = False

    tilt_pos: int = SERVO_CENTER_POS
    pan_speed: int = 0
    home_switch_pressed: bool = False

    pan_deg: Optional[float] = None
    tilt_deg: Optional[float] = 0.0

    tilt_zero_angle: Optional[float] = float(SERVO_CENTER_POS)

    def set_pan_home(self):
        self.pan_homed = True
        self.pan_speed = 0
        self.pan_deg = 0.0

    def set_tilt_zero(self, current_position: Optional[int] = None):
        if current_position is not None:
            self.tilt_pos = int(current_position)
        self.tilt_zeroed = True
        self.tilt_zero_angle = float(self.tilt_pos)
        self.tilt_deg = float(SERVO_CENTER_POS)

    def update_pan_status(self, speed: int, homed: bool, home_switch_pressed: bool):
        self.pan_speed = int(speed)
        self.pan_homed = bool(homed)
        self.home_switch_pressed = bool(home_switch_pressed)
        # The firmware has no pan encoder, so angle cannot be known while moving.
        self.pan_deg = 0.0 if self.pan_homed and self.pan_speed == 0 else None

    def update_tilt_position(self, pos: int):
        self.tilt_pos = int(pos)
        ref = self.tilt_zero_angle if self.tilt_zero_angle is not None else float(SERVO_CENTER_POS)
        self.tilt_deg = float(SERVO_CENTER_POS) + float(self.tilt_pos) - ref

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)
