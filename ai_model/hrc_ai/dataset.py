"""Sinh dataset tổng hợp CÓ NHÃN từ simulator (cùng logic thiết bị với firmware).

Chia tập THEO SEED (mỗi seed là 1 lần chạy độc lập) – KHÔNG chia ngẫu nhiên theo dòng, vì các cửa sổ
trượt chồng lấn nhau sẽ làm rò rỉ dữ liệu test vào train và cho metric ảo.

Chạy:  python -m hrc_ai.dataset          (ghi ai_model/data/{train,val,test}.csv)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from hrc_common.config import ROOT_DIR
from hrc_sim.device import MemoryNvs, SimDevice
from hrc_sim.offline import run_offline
from hrc_sim.scenarios import LABELS, make_scenario

from .features import WINDOW, compute_features

DATA_DIR = ROOT_DIR / "ai_model" / "data"
DATASET_KEY = bytes(range(32))  # chỉ để simulator ký; HMAC không ảnh hưởng dataset

SPLITS: dict[str, range] = {"train": range(0, 10), "val": range(100, 103), "test": range(200, 205)}
# Số chu kỳ (100 ms) mỗi kịch bản cho mỗi seed. sensor_noise nhiều hơn vì lỗi hiếm.
PLAN: dict[str, int] = {"normal": 3000, "approach": 3000, "overspeed": 3000, "sensor_noise": 6000, "mixed": 3000}

COLUMNS = ["run_id", "scenario", "seed", "seq", "ts_ms", "distance_mm", "v_robot_mmps", "zone", "estop",
           "true_distance_mm", "v_cmd_mmps", "fault", "label"]


def generate_run(scenario: str, seed: int, n_ticks: int) -> pd.DataFrame:
    """1 lần chạy độc lập: thiết bị mới (NVS rỗng), kịch bản có seed."""
    dev = SimDevice(1, DATASET_KEY, MemoryNvs())
    rows = []
    for s in run_offline(make_scenario(scenario, seed), dev, n_ticks):
        t, w = s.telemetry, s.sample
        rows.append((f"{scenario}:{seed}", scenario, seed, t.seq, t.ts_ms, t.distance_mm, t.v_robot_mmps, t.zone,
                     t.estop, w.true_distance_mm, w.v_cmd_mmps, w.fault or "", w.label))
    return pd.DataFrame(rows, columns=COLUMNS)


def generate_split(split: str) -> pd.DataFrame:
    # seed thực tế = seed_split * 10 + chỉ số kịch bản => mỗi kịch bản có dòng ngẫu nhiên riêng
    runs = [generate_run(sc, seed * 10 + i, n) for seed in SPLITS[split] for i, (sc, n) in enumerate(PLAN.items())]
    return pd.concat(runs, ignore_index=True)


def load_split(split: str, data_dir: Path = DATA_DIR, regenerate: bool = False) -> pd.DataFrame:
    path = data_dir / f"{split}.csv"
    if regenerate or not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        df = generate_split(split)
        df.to_csv(path, index=False)
        return df
    return pd.read_csv(path, keep_default_na=False)


def to_windows(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Cửa sổ trượt TRONG TỪNG lần chạy (không nối qua ranh giới 2 run).

    Trả về (X, y, meta):
        y                nhãn bản ghi CUỐI (trạng thái hiện tại) – dùng cho bộ phân loại
        meta.win_anomaly cửa sổ có ÍT NHẤT 1 bản ghi bất thường – đáp án cho IF/AE ("2 s qua có gì lạ?")
        meta.win_drift   cửa sổ chứa bản ghi drift (lỗi KHÔNG quan sát được bằng 1 cảm biến – xem README)
        meta.is_drift    bản ghi cuối là drift
    """
    xs, ys, metas = [], [], []
    for _, g in df.groupby("run_id", sort=False):
        if len(g) < WINDOW:
            continue
        xs.append(compute_features(g["distance_mm"].to_numpy(), g["v_robot_mmps"].to_numpy(),
                                   g["zone"].to_numpy(), g["estop"].to_numpy()))
        labels = g["label"].to_numpy()
        drift = (labels == "SENSOR_FAULT") & (g["fault"].to_numpy() == "drift")
        tail = g.iloc[WINDOW - 1:][["run_id", "scenario", "seq", "zone", "estop", "fault", "label"]].copy()
        tail["win_anomaly"] = sliding_window_view(labels != "NORMAL", WINDOW).any(axis=1)
        tail["win_drift"] = sliding_window_view(drift, WINDOW).any(axis=1)
        tail["is_drift"] = drift[WINDOW - 1:]
        ys.append(labels[WINDOW - 1:])
        metas.append(tail)
    return np.vstack(xs), np.concatenate(ys), pd.concat(metas, ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sinh dataset có nhãn từ simulator")
    ap.add_argument("--regenerate", action="store_true", help="sinh lại kể cả khi đã có file")
    args = ap.parse_args()
    for split in SPLITS:
        df = load_split(split, regenerate=args.regenerate)
        counts = df["label"].value_counts().reindex(LABELS, fill_value=0)
        print(f"[{split:5s}] {len(df):7d} bản ghi, {df['run_id'].nunique():3d} lần chạy | "
              + " ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
