#!/usr/bin/env python3
import math
import statistics
import threading
import time
from typing import Dict, List, Optional

import serial

from config import LIDAR_PORT

PORT = LIDAR_PORT
BAUD = 512000

START_SCAN = bytes([0xA5, 0x60])
STOP_SCAN = bytes([0xA5, 0x65])

MAX_RANGE_MM = 30000
MIN_RANGE_MM = 50
POINTS_TO_KEEP = 20000
FILTER_POINTS = 6000
FILTER_ANGLE_BINS = 720
FILTER_MIN_SAMPLES = 2


def parse_packet(pkt: bytes) -> List[Dict[str, float]]:
    if len(pkt) < 10:
        return []

    if pkt[0] != 0xAA or pkt[1] != 0x55:
        return []

    sample_count = pkt[3]
    expected_len = 10 + sample_count * 2
    if len(pkt) < expected_len:
        return []

    fsa_raw = pkt[4] | (pkt[5] << 8)
    lsa_raw = pkt[6] | (pkt[7] << 8)

    start_angle = (fsa_raw >> 1) / 64.0
    end_angle = (lsa_raw >> 1) / 64.0
    diff = end_angle - start_angle
    if diff < 0:
        diff += 360.0

    points = []
    for i in range(sample_count):
        off = 10 + i * 2
        d_raw = pkt[off] | (pkt[off + 1] << 8)
        # TG30/4ROS is a TOF lidar. Its packet distance is already in
        # millimetres; division by four and geometric angle correction apply
        # to YDLIDAR's older triangulation models, not this sensor.
        distance_mm = float(d_raw)

        if sample_count > 1:
            angle = start_angle + diff * i / (sample_count - 1)
        else:
            angle = start_angle

        if angle >= 360:
            angle -= 360

        if MIN_RANGE_MM <= distance_mm <= MAX_RANGE_MM:
            angle_rad = math.radians(angle)
            points.append({
                "angle_deg": round(angle, 2),
                "distance_mm": round(distance_mm, 2),
                "x": round(distance_mm * math.cos(angle_rad), 2),
                "y": round(distance_mm * math.sin(angle_rad), 2),
            })

    return points


def read_next_packet(ser: serial.Serial) -> Optional[bytes]:
    while True:
        b = ser.read(1)
        if not b:
            return None

        if b[0] != 0xAA:
            continue

        b2 = ser.read(1)
        if not b2:
            return None

        if b2[0] != 0x55:
            continue

        header_rest = ser.read(8)
        if len(header_rest) != 8:
            return None

        sample_count = header_rest[1]
        data_len = sample_count * 2
        data = ser.read(data_len)
        if len(data) != data_len:
            return None

        return bytes([0xAA, 0x55]) + header_rest + data


class LidarService:
    def __init__(
        self,
        port: str = PORT,
        baud: int = BAUD,
        max_range_mm: int = MAX_RANGE_MM,
        points_to_keep: int = POINTS_TO_KEEP,
    ):
        self.port = port
        self.baud = baud
        self.max_range_mm = max_range_mm
        self.points_to_keep = points_to_keep
        self._points: List[Dict[str, float]] = []
        self._latest_scan_points: List[Dict[str, float]] = []
        self._current_scan_points: List[Dict[str, float]] = []
        self._last_angle_deg: Optional[float] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_error: Optional[str] = None
        self._last_packet_at: Optional[float] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="lidar-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            raw_points = list(self._points[-FILTER_POINTS:])

        points = self._filter_points(raw_points)

        return {
            "ok": self._last_error is None,
            "running": self._running,
            "port": self.port,
            "baud": self.baud,
            "min_range_mm": MIN_RANGE_MM,
            "max_range_mm": self.max_range_mm,
            "points": points,
            "point_count": len(points),
            "last_packet_at": self._last_packet_at,
            "last_error": self._last_error,
        }

    def _filter_points(
        self,
        raw_points: List[Dict[str, float]],
    ) -> List[Dict[str, float]]:
        """Median-filter a few recent revolutions into stable angular bins."""
        distances_by_bin: List[List[float]] = [
            [] for _ in range(FILTER_ANGLE_BINS)
        ]
        for point in raw_points:
            angle_deg = point.get("angle_deg")
            distance_mm = point.get("distance_mm")
            if angle_deg is None or distance_mm is None:
                continue
            bin_index = int((angle_deg % 360.0) / 360.0 * FILTER_ANGLE_BINS)
            distances_by_bin[bin_index].append(distance_mm)

        points: List[Dict[str, float]] = []
        for bin_index, distances in enumerate(distances_by_bin):
            if len(distances) < FILTER_MIN_SAMPLES:
                continue

            distance_mm = statistics.median(distances)
            angle_deg = (bin_index + 0.5) * 360.0 / FILTER_ANGLE_BINS
            angle_rad = math.radians(angle_deg)
            points.append({
                "angle_deg": round(angle_deg, 2),
                "distance_mm": round(distance_mm, 2),
                "x": round(distance_mm * math.cos(angle_rad), 2),
                "y": round(distance_mm * math.sin(angle_rad), 2),
            })
        return points

    def _run(self) -> None:
        try:
            self._running = True
            self._last_error = None

            ser = serial.Serial(self.port, self.baud, timeout=1)
            try:
                ser.write(STOP_SCAN)
                time.sleep(0.3)
                ser.reset_input_buffer()
                ser.write(START_SCAN)
                time.sleep(0.5)

                while not self._stop_event.is_set():
                    pkt = read_next_packet(ser)
                    if not pkt:
                        continue

                    points = parse_packet(pkt)
                    if not points:
                        continue

                    with self._lock:
                        for point in points:
                            angle_deg = point["angle_deg"]
                            if (
                                self._last_angle_deg is not None
                                and angle_deg + 20 < self._last_angle_deg
                                and self._current_scan_points
                            ):
                                self._latest_scan_points = self._current_scan_points[-self.points_to_keep:]
                                self._current_scan_points = []

                            self._current_scan_points.append(point)
                            self._last_angle_deg = angle_deg

                        self._points.extend(points)
                        self._points = self._points[-self.points_to_keep:]
                    self._last_packet_at = time.time()
            finally:
                try:
                    ser.write(STOP_SCAN)
                    time.sleep(0.2)
                    ser.reset_input_buffer()
                except Exception:
                    pass
                ser.close()
        except Exception as exc:
            self._last_error = str(exc)
        finally:
            self._running = False


def main() -> None:
    lidar = LidarService()
    lidar.start()
    print("LiDAR service running. Press Ctrl+C to stop.")
    try:
        while True:
            snap = lidar.snapshot()
            print(
                f"running={snap['running']} points={snap['point_count']} "
                f"error={snap['last_error']}"
            )
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        lidar.stop()


if __name__ == "__main__":
    main()
