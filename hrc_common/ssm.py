"""Tính khoảng cách bảo vệ SSM và phân vùng – bản Python.

PHẢI cho kết quả giống hệt hàm C trong iot_code/firmware/include/hrc_params.h
(cùng công thức số nguyên, cùng cách làm tròn lên).
"""
from __future__ import annotations

from .config import ZONE_DANGER, ZONE_SAFE, ZONE_WARNING, SSMParams, ssm_params


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def protective_distance_mm(v_robot_mmps: int, p: SSMParams | None = None) -> int:
    """S = ceil((vH*(TR+TS) + vR*TR)/1000) + SS + C + ZR + ZS  (mm)."""
    p = p or ssm_params()
    if v_robot_mmps < 0:
        raise ValueError("v_robot_mmps phải >= 0 (dùng độ lớn tốc độ hướng về phía người)")
    dynamic = p.v_human_mmps * (p.t_react_ms + p.t_stop_ms) + v_robot_mmps * p.t_react_ms
    return _ceil_div(dynamic, 1000) + p.s_stop_mm + p.c_intrusion_mm + p.z_robot_mm + p.z_sensor_mm


def classify_zone(distance_mm: int, v_robot_mmps: int, p: SSMParams | None = None) -> int:
    """Trả về 0=SAFE, 1=WARNING, 2=DANGER. Biên (d == S) tính là vùng nguy hiểm hơn."""
    p = p or ssm_params()
    s = protective_distance_mm(v_robot_mmps, p)
    if distance_mm <= s:
        return ZONE_DANGER
    if distance_mm <= s + p.warning_margin_mm:
        return ZONE_WARNING
    return ZONE_SAFE
