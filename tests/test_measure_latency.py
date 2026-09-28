"""Bộ phân tích log Serial của scripts/measure_latency.py (không cần phần cứng)."""
from __future__ import annotations

import importlib.util
import sys

from hrc_common.config import ROOT_DIR

spec = importlib.util.spec_from_file_location("measure_latency", ROOT_DIR / "scripts" / "measure_latency.py")
ml = importlib.util.module_from_spec(spec)
sys.modules["measure_latency"] = ml  # dataclass cần module có trong sys.modules khi nạp từ đường dẫn
spec.loader.exec_module(ml)

SAMPLE = """\
=== HRC Safety Log – robot 1, config 40037d15ebf0e786, profile demo_tabletop ===
[OK] Tự kiểm tra HMAC đạt
LAT,5120,184,143,DANGER
[NET] MQTT ĐÃ KẾT NỐI
LAT,9870,201,8190,OVERSPEED
LAT,15000,176,62,DANGER
rác không liên quan, LAT sai định dạng
STATS,samples=1500,lat_mean_us=150,lat_max_us=410,tof_period_us=33400
EDGE,20100,4.812,1,612
EDGESTATS,runs=900,us_mean=540,us_max=780,anomalies=3,last_err=0.214
"""


def test_parse_and_summary():
    rep = ml.Report()
    for line in SAMPLE.splitlines():
        rep.feed(line)
    assert [t[0] for t in rep.trips] == [184, 201, 176]
    assert rep.trips[1][2] == "OVERSPEED"
    assert rep.stats == {"samples": 1500, "lat_mean_us": 150, "lat_max_us": 410, "tof_period_us": 33400}
    s = rep.summary()
    assert "Số lần ngắt       : 3" in s and "33.4 (cảm biến)" in s and "= 38.8 ms" in s
    assert rep.edge_events == [(4.812, 1, 612)]
    assert "trung bình 540 | max 780" in s and "báo bất thường 3 lần" in s


def test_summary_without_trips():
    assert "Chưa ghi nhận" in ml.Report().summary()
