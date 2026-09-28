"""Tiện ích cho test."""
from __future__ import annotations

from hrc_common.protocol import Telemetry, build_telemetry


class FakeClock:
    """Đồng hồ giả điều khiển được, trả về Unix ms."""

    def __init__(self, start_ms: int):
        self.now = start_ms

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def make_tel(key: bytes, seq: int, ts_ms: int, robot_id: int = 1, **over: int) -> Telemetry:
    fields = dict(robot_id=robot_id, seq=seq, ts_ms=ts_ms, distance_mm=500, v_robot_mmps=150, zone=0, estop=0)
    fields.update(over)
    return build_telemetry(key, **fields)
