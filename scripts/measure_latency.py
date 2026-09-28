"""NGHIỆM THU PHASE 5 (cần phần cứng): đo độ trễ fast lane "phát hiện -> ngắt relay" trên ESP32 thật.

Firmware in mỗi lần ngắt:   LAT,<millis>,<µs từ lúc có mẫu ToF tới lúc đã ghi chân relay>,<distance_mm>,<lý do>
và khi nhận lệnh "stats":   STATS,samples=..,lat_mean_us=..,lat_max_us=..,tof_period_us=..[,meas_max_us=..]

Độ trễ phát hiện TỆ NHẤT (ước lượng) = chu kỳ ToF (mẫu vừa lỡ) + xử lý (đo được) + thời gian nhả relay (datasheet).
Trong lúc chạy: đưa tay/vật vào vùng nguy hiểm nhiều lần, thử cả nút E-Stop, gõ "v 400" để thử vượt tốc.

Chạy:  python scripts/measure_latency.py --port COM5 --duration 60
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from dataclasses import dataclass, field

RELAY_RELEASE_MS_ASSUMED = 5.0  # GIẢ ĐỊNH: relay 5V loại SRD-05VDC nhả <= 5 ms – kiểm tra datasheet module thực tế

LAT_RE = re.compile(r"^LAT,(\d+),(\d+),(\d+),(\w+)\s*$")
STATS_RE = re.compile(r"^STATS,(.*)$")
EDGESTATS_RE = re.compile(r"^EDGESTATS,(.*)$")
EDGE_RE = re.compile(r"^EDGE,(\d+),([\d.]+),([01]),(\d+)\s*$")


@dataclass
class Report:
    trips: list[tuple[int, int, str]] = field(default_factory=list)  # (lat_us, distance_mm, reason)
    stats: dict[str, int] = field(default_factory=dict)
    edge_stats: dict[str, float] = field(default_factory=dict)
    edge_events: list[tuple[float, int, int]] = field(default_factory=list)  # (mse, anomaly, µs)

    def feed(self, line: str) -> None:
        if m := LAT_RE.match(line.strip()):
            self.trips.append((int(m.group(2)), int(m.group(3)), m.group(4)))
        elif m := STATS_RE.match(line.strip()):
            self.stats = {k: int(v) for k, v in (kv.split("=") for kv in m.group(1).split(","))}
        elif m := EDGESTATS_RE.match(line.strip()):
            self.edge_stats = {k: float(v) for k, v in (kv.split("=") for kv in m.group(1).split(","))}
        elif m := EDGE_RE.match(line.strip()):
            self.edge_events.append((float(m.group(2)), int(m.group(3)), int(m.group(4))))

    def summary(self) -> str:
        out = ["=" * 64, " ĐỘ TRỄ FAST LANE TRÊN ESP32 (phát hiện -> ghi chân relay)", "=" * 64]
        if self.trips:
            lats = sorted(t[0] for t in self.trips)
            p95 = lats[min(len(lats) - 1, int(0.95 * len(lats)))]
            by_reason: dict[str, int] = {}
            for _, _, r in self.trips:
                by_reason[r] = by_reason.get(r, 0) + 1
            out += [f" Số lần ngắt       : {len(lats)}  {by_reason}",
                    f" Xử lý (µs)        : trung bình {statistics.mean(lats):.0f} | p95 {p95} | max {lats[-1]}"]
        else:
            out.append(" Chưa ghi nhận lần ngắt nào – hãy đưa vật vào vùng nguy hiểm khi đang đo")
        if self.stats:
            period_ms = self.stats.get("tof_period_us", 0) / 1000
            proc_ms = self.stats.get("lat_max_us", 0) / 1000
            from hrc_common.config import ssm_params

            out += [f" Tất cả mẫu        : {self.stats.get('samples', 0)} mẫu, xử lý TB "
                    f"{self.stats.get('lat_mean_us', 0)} µs, max {self.stats.get('lat_max_us', 0)} µs",
                    f" Chu kỳ cảm biến   : {period_ms:.1f} ms"]
            if "meas_max_us" in self.stats:  # HC-SR04: thời gian 1 lần đo (chờ tiếng vọng) lớn nhất
                out.append(f" Đo 1 lần (max)    : {self.stats['meas_max_us'] / 1000:.1f} ms (chờ tiếng vọng, đã nằm trong chu kỳ)")
            out += [f" TỆ NHẤT (ước lượng): {period_ms:.1f} (cảm biến) + {proc_ms:.2f} (xử lý) + "
                    f"{RELAY_RELEASE_MS_ASSUMED:.0f} (relay, giả định) = {period_ms + proc_ms + RELAY_RELEASE_MS_ASSUMED:.1f} ms",
                    f" So với giả định TR = {ssm_params().t_react_ms} ms trong config/hrc_config.toml "
                    "-> cập nhật t_react_ms nếu cần"]
        if self.edge_stats:
            es = self.edge_stats
            out += ["-" * 64, " EDGE AI INT8 TRÊN ESP32 (đặc trưng + autoencoder, chạy ở core 0, 10 Hz)",
                    f" Số lần suy luận    : {int(es.get('runs', 0))}, báo bất thường {int(es.get('anomalies', 0))} lần",
                    f" Thời gian (µs)     : trung bình {int(es.get('us_mean', 0))} | max {int(es.get('us_max', 0))} "
                    f"(ngân sách 100 000 µs/chu kỳ)"]
        return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="cổng Serial, vd COM5 (Windows) hoặc /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--duration", type=float, default=60)
    args = ap.parse_args()
    try:
        import serial  # pyserial: pip install -e ".[hw]"
    except ImportError:
        print("[LỖI] Thiếu pyserial: pip install -e \".[hw]\"")
        return 2
    rep = Report()
    with serial.Serial(args.port, args.baud, timeout=0.5) as ser:
        print(f"Đang đo {args.duration:.0f} s trên {args.port} – đưa vật vào vùng nguy hiểm vài lần...")
        end = time.monotonic() + args.duration
        while time.monotonic() < end:
            line = ser.readline().decode("utf-8", errors="replace")
            if line:
                rep.feed(line)
                if line.startswith("LAT,"):
                    print("  " + line.strip())
        ser.write(b"stats\n")
        t = time.monotonic() + 2
        while time.monotonic() < t and not (rep.stats and rep.edge_stats):  # firmware in STATS rồi EDGESTATS
            rep.feed(ser.readline().decode("utf-8", errors="replace"))
    print(rep.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
