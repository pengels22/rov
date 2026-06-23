from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from config import PAN_SERVO_ID, TILT_SERVO_ID, SERVO_CENTER_POS


@dataclass
class TurretState:
    pan_servo_id: int = PAN_SERVO_ID
    tilt_servo_id: int = TILT_SERVO_ID

    pan_homed: bool = False
    tilt_zeroed: bool = False

    pan_pos: int = SERVO_CENTER_POS
    tilt_pos: int = SERVO_CENTER_POS

    pan_deg: Optional[float] = 0.0
    tilt_deg: Optional[float] = 0.0

    pan_home_angle: Optional[float] = float(SERVO_CENTER_POS)
    tilt_zero_angle: Optional[float] = float(SERVO_CENTER_POS)

    def set_pan_home(self, current_position: Optional[int] = None):
        if current_position is not None:
            self.pan_pos = int(current_position)
        self.pan_homed = True
        self.pan_home_angle = float(self.pan_pos)
        self.pan_deg = 0.0

    def set_tilt_zero(self, current_position: Optional[int] = None):
        if current_position is not None:
            self.tilt_pos = int(current_position)
        self.tilt_zeroed = True
        self.tilt_zero_angle = float(self.tilt_pos)
        self.tilt_deg = 0.0

    def update_pan_position(self, pos: int):
        self.pan_pos = int(pos)
        ref = self.pan_home_angle if self.pan_home_angle is not None else float(SERVO_CENTER_POS)
        self.pan_deg = float(self.pan_pos) - ref

    def update_tilt_position(self, pos: int):
        self.tilt_pos = int(pos)
        ref = self.tilt_zero_angle if self.tilt_zero_angle is not None else float(SERVO_CENTER_POS)
        self.tilt_deg = float(self.tilt_pos) - ref

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)
