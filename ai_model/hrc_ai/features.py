"""Trích đặc trưng từ cửa sổ trượt telemetry – DÙNG CHUNG cho huấn luyện và suy luận (backend).

Mỗi cửa sổ = WINDOW bản ghi liên tiếp gần nhất (mặc định 20 x 100 ms = 2 s), chỉ nhìn QUÁ KHỨ
(nhân quả) nên chạy được thời gian thực. Nhãn của cửa sổ = nhãn của bản ghi CUỐI.

Chỉ dùng phép toán đơn giản (min/max/mean/std/hồi quy tuyến tính) để Phase 7 có thể viết lại bằng C
trên ESP32. Mọi hằng số đều lấy từ config dùng chung.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from hrc_common.config import load_config, ssm_params

TOF_OUT_OF_RANGE_MM = 8190  # trùng hrc_sim.scenarios (không import để backend không phụ thuộc simulator)
WINDOW = 20
# Tốc độ thay đổi khoảng cách vượt ngưỡng này thì người thật không làm được => dấu hiệu lỗi cảm biến.
# GIẢ ĐỊNH: 2000 mm/s (ISO 13855 dùng 2000 mm/s cho tốc độ tay; ở mô hình bàn vH = 300 mm/s)
JUMP_MMPS = 2000

FEATURE_NAMES = (
    "d_last",            # khoảng cách mới nhất (đã cắt ở D_CLIP)
    "d_min",
    "d_mean",
    "d_std",
    "d_slope_mmps",      # hồi quy tuyến tính: âm = đang tiến lại gần
    "dd_last_mmps",      # tốc độ thay đổi ở bước cuối
    "dd_absmax_mmps",    # tốc độ thay đổi lớn nhất trong cửa sổ
    "n_jumps",           # số bước thay đổi nhanh hơn JUMP_MMPS
    "oor_frac",          # tỉ lệ mẫu "không thấy vật" (8190)
    "oor_last",
    "n_abrupt_oor",      # số lần đang thấy vật ở GẦN mà đột ngột mất mục tiêu (dropout)
    "v_last",
    "v_max",
    "v_mean",
    "estop_frac",
    "estop_last",
    "zone_last",
    "danger_frac",
    "resid_std",         # độ "rung" quanh đường hồi quy (nhiễu)
    "d_last_minus_med",  # mẫu cuối lệch khỏi trung vị (spike)
)
N_FEATURES = len(FEATURE_NAMES)


def feature_fingerprint() -> str:
    """Dấu vân tay CHỈ của phần config ảnh hưởng tới dữ liệu/đặc trưng (SSM, chu kỳ, fast lane, cửa sổ).

    Đổi các mục khác (chain, incident, mqtt...) không làm model lỗi thời => không cần huấn luyện lại.
    """
    cfg = load_config()
    relevant = {
        "ssm": asdict(ssm_params()),
        "telemetry_period_ms": cfg["system"]["telemetry_period_ms"],
        "fastlane": cfg["fastlane"],
        "window": WINDOW,
        "jump_mmps": JUMP_MMPS,
        "features": FEATURE_NAMES,
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]


def _constants() -> tuple[float, float, int]:
    p = ssm_params()
    clip = float(p.distance_valid_max_mm + 100)  # 8190 được cắt về đây để không lấn át thống kê
    abrupt_below = float(p.distance_valid_max_mm - 200)  # "đang ở gần" = dưới ngưỡng này
    period_ms = load_config()["system"]["telemetry_period_ms"]
    return clip, abrupt_below, period_ms


def compute_features(distance_mm, v_robot_mmps, zone, estop, window: int = WINDOW) -> np.ndarray:
    """Trả về ma trận (N - window + 1, N_FEATURES) float32 cho mọi cửa sổ kết thúc tại i >= window-1.

    Suy luận online: truyền đúng `window` bản ghi cuối => nhận về 1 hàng.
    """
    d = np.asarray(distance_mm, dtype=np.float64)
    if d.shape[0] < window:
        raise ValueError(f"cần ít nhất {window} bản ghi, nhận {d.shape[0]}")
    clip, abrupt_below, period_ms = _constants()
    dt = period_ms / 1000.0

    oor = d >= TOF_OUT_OF_RANGE_MM
    dc = np.minimum(d, clip)
    D = sliding_window_view(dc, window)
    O = sliding_window_view(oor, window)
    V = sliding_window_view(np.asarray(v_robot_mmps, dtype=np.float64), window)
    Z = sliding_window_view(np.asarray(zone, dtype=np.float64), window)
    E = sliding_window_view(np.asarray(estop, dtype=np.float64), window)

    diffs = np.diff(D, axis=1) / dt  # mm/s
    t = (np.arange(window) - (window - 1) / 2.0) * dt  # thời gian đã trừ trung bình
    d_mean = D.mean(axis=1)
    slope = (D - d_mean[:, None]) @ t / float(t @ t)
    resid = D - (d_mean[:, None] + slope[:, None] * t)
    abrupt = (~O[:, :-1]) & (D[:, :-1] < abrupt_below) & O[:, 1:]

    feats = np.column_stack([
        D[:, -1],
        D.min(axis=1),
        d_mean,
        D.std(axis=1),
        slope,
        diffs[:, -1],
        np.abs(diffs).max(axis=1),
        (np.abs(diffs) > JUMP_MMPS).sum(axis=1),
        O.mean(axis=1),
        O[:, -1],
        abrupt.sum(axis=1),
        V[:, -1],
        V.max(axis=1),
        V.mean(axis=1),
        E.mean(axis=1),
        E[:, -1],
        Z[:, -1],
        (Z == 2).mean(axis=1),
        resid.std(axis=1),
        D[:, -1] - np.median(D, axis=1),
    ])
    return feats.astype(np.float32)
