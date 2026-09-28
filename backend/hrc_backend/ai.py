"""Lớp AI của backend: nạp mô hình và tự kiểm tra (Phase 4 gọi HrcDetector.update cho mỗi telemetry).

Chạy:  hrc-ai-selftest
"""
from __future__ import annotations

import logging
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from hrc_ai.features import WINDOW
from hrc_ai.inference import MODEL_DIR, HrcDetector
from hrc_sim.device import MemoryNvs, SimDevice
from hrc_sim.offline import run_offline
from hrc_sim.scenarios import make_scenario

SELFTEST_KEY = bytes(range(32))


def load_detector(model_dir: Path | None = None) -> HrcDetector:
    return HrcDetector(model_dir or MODEL_DIR)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    t0 = time.perf_counter()
    try:
        det = load_detector()
    except (FileNotFoundError, ValueError) as e:
        print(f"[LỖI] {e}")
        return 1
    m = det.meta
    print(f"[OK] Nạp model trong {(time.perf_counter() - t0) * 1000:.0f} ms (huấn luyện {m['created_at']}, "
          f"sklearn {m['sklearn_version']}, cửa sổ {m['window']}, nhãn {det.labels})")

    # Phát lại vài kịch bản (seed khác tập train). Bảng tổng hợp chấm theo lô cho nhanh.
    print(f"  {'kịch bản':<14}{'DANGER':>8}{'AI xác nhận':>13}{'bất thường':>12}  nhãn dự đoán")
    for name in ("normal", "approach", "overspeed", "sensor_noise"):
        dev = SimDevice(1, SELFTEST_KEY, MemoryNvs())
        tels = [s.telemetry for s in run_offline(make_scenario(name, 4242), dev, 1200)]
        s = det.score_sequence(tels)
        zones = np.array([t.zone for t in tels[WINDOW - 1:]])
        danger = int((zones == 2).sum())
        confirmed = int(((zones == 2) & (s["label"] == "INTRUSION")).sum())
        anomalies = int((s["if_anomaly"] | s["ae_anomaly"] | (s["label"] != "NORMAL")).sum())
        print(f"  {name:<14}{danger:>8}{confirmed:>13}{anomalies:>12}  {dict(Counter(s['label'].tolist()))}")

    # Đo đúng đường đi của backend thật: update() từng bản ghi
    tels = [s.telemetry for s in run_offline(make_scenario("mixed", 4243), SimDevice(1, SELFTEST_KEY), 220)]
    times = []
    for t in tels:
        t0 = time.perf_counter()
        det.update(t)
        times.append((time.perf_counter() - t0) * 1000)
    arr = np.asarray(times[WINDOW:])
    print(f"[OK] Độ trễ update() mỗi bản ghi: trung bình {arr.mean():.1f} ms, p95 {np.percentile(arr, 95):.1f} ms "
          f"(chu kỳ telemetry 100 ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
