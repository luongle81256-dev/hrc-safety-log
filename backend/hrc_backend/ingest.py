"""Tiếp nhận bản tin từ MQTT: kiểm tra -> lưu log off-chain / loại bỏ kèm lý do.

Thứ tự kiểm tra telemetry (rẻ trước, đắt sau; HMAC TRƯỚC replay để kẻ lạ không dò được trạng thái):
    topic -> robot được phép -> kích thước/JSON/schema -> robot_id khớp topic -> HMAC
    -> replay (seq <= seq cuối) -> ts_ms không ở tương lai -> lưu (+ ghi khoảng trống seq nếu có)
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from hrc_common.config import config_hash, load_config
from hrc_common.protocol import (
    Heartbeat,
    LastWill,
    ProtocolError,
    Telemetry,
    parse_heartbeat,
    parse_telemetry,
    parse_topic,
    verify_heartbeat,
    verify_telemetry,
)

from .storage import DeviceStatus, LogStore

log = logging.getLogger("hrc-ingest")

# Mã lý do bổ sung (ngoài các mã R_* của hrc_common.protocol)
R_KIND = "KIND"  # topic không phải loại bản tin backend nhận
R_ROBOT_UNKNOWN = "ROBOT_UNKNOWN"  # robot_id không có trong cấu hình
R_ROBOT_MISMATCH = "ROBOT_MISMATCH"  # robot_id trong payload khác topic
R_REPLAY = "REPLAY"  # seq/ts_ms không mới hơn bản tin trước
R_FUTURE_TS = "FUTURE_TS"  # ts_ms vượt quá giờ backend


def now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: bool
    reason: str | None = None
    detail: str = ""
    telemetry: Telemetry | None = None
    gap: tuple[int, int] | None = None
    event: str | None = None  # heartbeat: "ONLINE" | "OFFLINE" | "CFG_MISMATCH"


class TelemetryIngestor:
    def __init__(self, store: LogStore, key: bytes, clock: Callable[[], int] = now_ms):
        cfg = load_config()
        self.store = store
        self.key = key
        self.clock = clock
        self.allowed = set(cfg["system"]["robot_ids"])
        self.max_future_ms = cfg["security"]["ts_max_future_skew_ms"]

    def _reject(self, now: int, topic: str, payload: bytes, reason: str, detail: str,
                robot_id: int | None = None, seq: int | None = None) -> IngestResult:
        self.store.insert_rejection(now, topic, reason, detail, payload, robot_id, seq)
        log.warning("LOẠI [%s] topic=%s %s", reason, topic, detail)
        return IngestResult(False, reason, detail)

    def ingest(self, topic: str, payload: bytes) -> IngestResult:
        now = self.clock()
        try:
            robot_id, kind = parse_topic(topic)
            if kind != "telemetry":
                return self._reject(now, topic, payload, R_KIND, f"không nhận loại '{kind}'")
            if robot_id not in self.allowed:
                return self._reject(now, topic, payload, R_ROBOT_UNKNOWN, f"robot {robot_id} chưa đăng ký")
            t = parse_telemetry(payload)
        except ProtocolError as e:
            return self._reject(now, topic, payload, e.reason, e.detail)

        if t.robot_id != robot_id:
            return self._reject(now, topic, payload, R_ROBOT_MISMATCH,
                                f"payload robot {t.robot_id} != topic {robot_id}", robot_id, t.seq)
        if not verify_telemetry(self.key, t):
            return self._reject(now, topic, payload, "HMAC", f"sai chữ ký seq={t.seq}", robot_id, t.seq)

        last = self.store.last_seq(robot_id)
        if last is not None and t.seq <= last:
            return self._reject(now, topic, payload, R_REPLAY, f"seq {t.seq} <= seq cuối {last}", robot_id, t.seq)
        if t.ts_ms > now + self.max_future_ms:
            return self._reject(now, topic, payload, R_FUTURE_TS,
                                f"ts_ms vượt giờ backend {t.ts_ms - now} ms", robot_id, t.seq)

        if not self.store.insert_telemetry(t, now):  # phòng 2 luồng chèn cùng lúc
            return self._reject(now, topic, payload, R_REPLAY, f"seq {t.seq} đã tồn tại", robot_id, t.seq)
        gap = None
        if last is not None and t.seq > last + 1:
            gap = (last + 1, t.seq - 1)
            self.store.insert_gap(robot_id, gap[0], gap[1], now)
            log.info("Robot %d mất %d bản tin (seq %d..%d)", robot_id, gap[1] - gap[0] + 1, *gap)
        return IngestResult(True, telemetry=t, gap=gap)


class HeartbeatMonitor:
    """Theo dõi sống/chết của thiết bị: heartbeat có ký, LWT, và timeout."""

    def __init__(self, store: LogStore, key: bytes, clock: Callable[[], int] = now_ms):
        cfg = load_config()
        self.store = store
        self.key = key
        self.clock = clock
        self.allowed = set(cfg["system"]["robot_ids"])
        self.timeout_ms = cfg["system"]["failsafe_timeout_ms"]
        self.cfg_hash = config_hash()
        self.status: dict[int, DeviceStatus] = store.device_statuses()

    def _set(self, st: DeviceStatus, now: int) -> None:
        self.status[st.robot_id] = st
        self.store.upsert_device_status(st, now)

    def _reject(self, now: int, topic: str, payload: bytes, reason: str, detail: str,
                robot_id: int | None = None) -> IngestResult:
        self.store.insert_rejection(now, topic, reason, detail, payload, robot_id)
        log.warning("LOẠI heartbeat [%s] topic=%s %s", reason, topic, detail)
        return IngestResult(False, reason, detail)

    def ingest(self, topic: str, payload: bytes) -> IngestResult:
        now = self.clock()
        try:
            robot_id, kind = parse_topic(topic)
            if kind != "heartbeat":
                return self._reject(now, topic, payload, R_KIND, f"không nhận loại '{kind}'")
            if robot_id not in self.allowed:
                return self._reject(now, topic, payload, R_ROBOT_UNKNOWN, f"robot {robot_id} chưa đăng ký", robot_id)
            msg = parse_heartbeat(payload)
        except ProtocolError as e:
            return self._reject(now, topic, payload, e.reason, e.detail)

        if msg.robot_id != robot_id:
            return self._reject(now, topic, payload, R_ROBOT_MISMATCH, "robot_id khác topic", robot_id)
        prev = self.status.get(robot_id)

        if isinstance(msg, LastWill):
            # LWT không ký: chỉ có thể gây báo động nhầm (nghiêng về an toàn), không che giấu được sự cố
            self._set(DeviceStatus(robot_id, False, prev and prev.last_hb_ts_ms, prev and prev.last_seen_at_ms,
                                   prev and prev.state, prev and prev.estop, prev and prev.cfg,
                                   prev and prev.cfg_match, "LWT"), now)
            log.error("Robot %d MẤT KẾT NỐI (LWT)", robot_id)
            return IngestResult(True, event="OFFLINE")

        hb: Heartbeat = msg
        if not verify_heartbeat(self.key, hb):
            return self._reject(now, topic, payload, "HMAC", "sai chữ ký heartbeat", robot_id)
        if prev and prev.last_hb_ts_ms is not None and hb.ts_ms <= prev.last_hb_ts_ms:
            return self._reject(now, topic, payload, R_REPLAY,
                                f"ts_ms {hb.ts_ms} <= heartbeat trước {prev.last_hb_ts_ms}", robot_id)

        cfg_match = hb.cfg == self.cfg_hash
        self._set(DeviceStatus(robot_id, True, hb.ts_ms, now, hb.state, hb.estop, hb.cfg, cfg_match, None), now)
        if not cfg_match and (prev is None or prev.cfg_match is not False):
            log.error("Robot %d dùng config khác backend (%s != %s) – kiểm tra lại firmware",
                      robot_id, hb.cfg, self.cfg_hash)
            return IngestResult(True, event="CFG_MISMATCH")
        if prev is None or not prev.online:
            log.info("Robot %d ONLINE", robot_id)
            return IngestResult(True, event="ONLINE")
        return IngestResult(True)

    def check_timeouts(self) -> list[int]:
        """Đánh dấu OFFLINE các robot quá failsafe_timeout_ms không có heartbeat hợp lệ."""
        now = self.clock()
        went_offline = []
        for rid, st in list(self.status.items()):
            if st.online and st.last_seen_at_ms is not None and now - st.last_seen_at_ms > self.timeout_ms:
                self._set(DeviceStatus(rid, False, st.last_hb_ts_ms, st.last_seen_at_ms, st.state, st.estop,
                                       st.cfg, st.cfg_match, "TIMEOUT"), now)
                log.error("Robot %d MẤT KẾT NỐI (quá %d ms không có heartbeat)", rid, self.timeout_ms)
                went_offline.append(rid)
        return went_offline
