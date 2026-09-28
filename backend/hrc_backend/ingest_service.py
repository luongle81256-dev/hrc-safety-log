"""Dịch vụ ingest: subscribe MQTT -> kiểm tra -> SQLite. (Phase 4 bổ sung AI, Merkle, chain.)

Chạy:  hrc-ingest            (hoặc: python -m hrc_backend.ingest_service)
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import paho.mqtt.client as mqtt

from hrc_common.config import load_config
from hrc_common.env import mqtt_settings

from .ingest import HeartbeatMonitor, TelemetryIngestor
from .settings import hmac_key, open_store, sqlite_path

log = logging.getLogger("hrc-ingest")
ZONE_NAMES = ("SAFE", "WARNING", "DANGER")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HRC backend – MQTT ingest")
    ap.add_argument("--db", type=Path, help="đường dẫn SQLite (mặc định SQLITE_PATH trong .env)")
    ap.add_argument("--stats-every", type=float, default=5.0, help="giây giữa 2 lần in thống kê")
    ap.add_argument("--duration", type=float, default=0, help="giây chạy (0 = chạy mãi)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    try:
        key = hmac_key()
    except ValueError as e:
        log.error("%s", e)
        return 2
    cfg = load_config()
    db_path = args.db or sqlite_path()
    store = open_store(db_path)
    tel_ingest = TelemetryIngestor(store, key)
    hb_monitor = HeartbeatMonitor(store, key)
    stats: Counter[str] = Counter()
    last_tel: dict[int, object] = {}
    stats_lock = threading.Lock()

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Broker từ chối kết nối: %s", reason_code)
            return
        c.subscribe([("hrc/+/telemetry", cfg["mqtt"]["qos_telemetry"]),
                     ("hrc/+/heartbeat", cfg["mqtt"]["qos_heartbeat"])])
        log.info("Đã kết nối broker, subscribe hrc/+/telemetry và hrc/+/heartbeat")

    def on_message(c, userdata, msg):
        try:
            if msg.topic.endswith("/heartbeat"):
                r = hb_monitor.ingest(msg.topic, msg.payload)
                key_name = "hb_ok" if r.accepted else f"hb_{r.reason}"
            else:
                r = tel_ingest.ingest(msg.topic, msg.payload)
                key_name = "ok" if r.accepted else r.reason
                t = r.telemetry
                if t:
                    prev = last_tel.get(t.robot_id)
                    last_tel[t.robot_id] = t
                    if prev is None or prev.zone != t.zone or prev.estop != t.estop:  # chỉ log khi đổi trạng thái
                        (log.warning if t.zone == 2 or t.estop else log.info)(
                            "Robot %d -> %s estop=%d d=%dmm v=%dmm/s (seq %d)",
                            t.robot_id, ZONE_NAMES[t.zone], t.estop, t.distance_mm, t.v_robot_mmps, t.seq)
            with stats_lock:
                stats[key_name] += 1
        except Exception:  # không để 1 bản tin lỗi làm chết vòng lặp MQTT
            log.exception("Lỗi xử lý bản tin topic=%s", msg.topic)

    s = mqtt_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hrc-backend-ingest")
    if s.username:
        client.username_pw_set(s.username, s.password)
    client.on_connect, client.on_message = on_connect, on_message
    client.connect_async(s.host, s.port, keepalive=cfg["mqtt"]["keepalive_s"])
    client.loop_start()
    log.info("Ingest chạy: broker %s:%s, DB %s", s.host, s.port, db_path)

    start = next_stats = time.monotonic()
    try:
        while args.duration <= 0 or time.monotonic() - start < args.duration:
            time.sleep(0.5)
            hb_monitor.check_timeouts()
            if time.monotonic() >= next_stats:
                next_stats += args.stats_every
                with stats_lock:
                    snap = dict(stats)
                log.info("Thống kê: %s | DB %d bản ghi", snap or "chưa có bản tin", store.count_telemetry())
                for rid, t in sorted(last_tel.items()):
                    st = hb_monitor.status.get(rid)
                    log.info("  robot %d: seq=%d %s d=%dmm estop=%d | %s", rid, t.seq, ZONE_NAMES[t.zone],
                             t.distance_mm, t.estop,
                             "ONLINE" if st and st.online else f"OFFLINE({st.offline_reason if st else '?'})")
    except KeyboardInterrupt:
        log.info("Dừng theo yêu cầu (Ctrl+C)")
    finally:
        client.loop_stop()
        client.disconnect()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
