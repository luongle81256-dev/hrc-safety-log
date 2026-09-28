"""Bản sao logic firmware ESP32 (fast lane + bảo mật lệnh + NVS) – KHÔNG phụ thuộc MQTT.

Firmware C ở Phase 5 phải cài đặt ĐÚNG hành vi này; tests/test_simulator.py là đặc tả sống.

Fast lane mỗi chu kỳ:
    1. v_meas = tốc độ đã áp dụng ở chu kỳ trước (ESP32 đo từ chuyển động thật của servo)
    2. zone   = SSM(distance_đo, v_meas)
    3. NGẮT relay ngay nếu: zone DANGER | v_meas > v_limit | LOCKED | mất MQTT > failsafe_timeout
    4. Relay đang ngắt chỉ đóng lại khi: SAFE liên tục >= estop_reset_hold_ms VÀ không LOCKED
    5. Tốc độ chu kỳ tới: 0 nếu ngắt; giảm theo warning_speed_percent nếu WARNING; ngược lại = lệnh
Khởi động ở trạng thái NGẮT relay (khởi động an toàn); trạng thái LOCKED được lưu NVS nên reset không gỡ được khóa.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from hrc_common.config import ZONE_DANGER, ZONE_SAFE, ZONE_WARNING, SSMParams, config_hash, load_config, ssm_params
from hrc_common.protocol import (
    CMD_LOCK,
    Heartbeat,
    ProtocolError,
    Telemetry,
    build_heartbeat,
    build_telemetry,
    parse_command,
    verify_command,
)
from hrc_common.ssm import classify_zone

# Lý do thiết bị từ chối lệnh
CMD_OK = "OK"
CMD_BAD_FORMAT = "BAD_FORMAT"
CMD_BAD_HMAC = "BAD_HMAC"
CMD_REPLAY = "REPLAY"
CMD_STALE = "STALE"


# =============================================================================
# NVS giả lập
# =============================================================================

class MemoryNvs:
    """NVS trong RAM (dùng cho test)."""

    def __init__(self, data: dict | None = None):
        self.data = dict(data or {})

    def load(self) -> dict:
        return dict(self.data)

    def save(self, **kv: int | bool) -> None:
        self.data.update(kv)


class FileNvs(MemoryNvs):
    """NVS lưu ra file JSON, ghi nguyên tử (tmp + replace) giống flash không bị ghi dở."""

    def __init__(self, path: Path):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        super().__init__(data)

    def save(self, **kv: int | bool) -> None:
        super().save(**kv)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)


# =============================================================================
# Thiết bị
# =============================================================================

class SimDevice:
    def __init__(self, robot_id: int, key: bytes, nvs: MemoryNvs | None = None,
                 params: SSMParams | None = None, ntp_synced: bool = True):
        cfg = load_config()
        self.robot_id = robot_id
        self.key = key
        self.nvs = nvs or MemoryNvs()
        self.p = params or ssm_params()
        self.hold_ms = cfg["fastlane"]["estop_reset_hold_ms"]
        self.warn_pct = cfg["fastlane"]["warning_speed_percent"]
        self.cmd_max_skew_ms = cfg["security"]["cmd_max_skew_ms"]
        self.seq_block = cfg["system"]["seq_reserve_block"]
        self.cfg_hash = config_hash()
        self.ntp_synced = ntp_synced

        # ---- khởi động: đọc NVS, đặt trước 1 khối seq ----
        stored = self.nvs.load()
        self.seq = int(stored.get("seq_reserved", 1))
        self.seq_limit = self.seq + self.seq_block
        self.last_nonce = int(stored.get("last_nonce", 0))
        self.locked = bool(stored.get("locked", False))
        self.nvs.save(seq_reserved=self.seq_limit)

        self.estop = True  # khởi động an toàn: relay ngắt cho tới khi SAFE đủ lâu
        self.estop_reason = "BOOT"
        self.safe_since_ms: int | None = None
        self.v_applied = 0  # tốc độ đang áp dụng cho servo
        self.zone = ZONE_SAFE

    # ---- seq bền qua reset ----
    def _next_seq(self) -> int:
        s = self.seq
        self.seq += 1
        if self.seq >= self.seq_limit:
            self.seq_limit += self.seq_block
            self.nvs.save(seq_reserved=self.seq_limit)
        return s

    def _trip(self, reason: str) -> None:
        self.estop = True
        self.estop_reason = reason
        self.safe_since_ms = None

    def force_estop(self, reason: str) -> None:
        """Ngắt từ bên ngoài logic SSM (mất kết nối MQTT, nút E-Stop vật lý...)."""
        self._trip(reason)

    # ---- fast lane ----
    def tick(self, now_ms: int, measured_distance_mm: int, v_cmd_mmps: int) -> Telemetry:
        v_meas = self.v_applied
        zone = classify_zone(measured_distance_mm, v_meas, self.p)

        if zone == ZONE_DANGER:
            self._trip("DANGER")
        elif v_meas > self.p.v_robot_limit_mmps:
            self._trip("OVERSPEED")
        elif self.locked:
            self._trip("LOCKED")
        elif self.estop:
            if zone == ZONE_SAFE:
                if self.safe_since_ms is None:
                    self.safe_since_ms = now_ms
                if now_ms - self.safe_since_ms >= self.hold_ms:
                    self.estop = False
                    self.estop_reason = ""
            else:
                self.safe_since_ms = None

        if self.estop:
            self.v_applied = 0
        elif zone == ZONE_WARNING:
            self.v_applied = v_cmd_mmps * self.warn_pct // 100
        else:
            self.v_applied = v_cmd_mmps
        self.zone = zone

        # Telemetry là ảnh chụp ĐÚNG các giá trị dùng để quyết định => backend tính lại zone sẽ khớp
        return build_telemetry(
            self.key,
            robot_id=self.robot_id,
            seq=self._next_seq(),
            ts_ms=now_ms,
            distance_mm=min(measured_distance_mm, 65535),
            v_robot_mmps=min(v_meas, 65535),
            zone=zone,
            estop=int(self.estop),
        )

    # ---- lệnh từ backend ----
    def handle_command(self, payload: bytes | str, now_ms: int) -> str:
        """Xử lý lệnh LOCK/UNLOCK. Trả về CMD_OK hoặc lý do từ chối (lệnh bị bỏ qua)."""
        try:
            c = parse_command(payload)
        except ProtocolError:
            return CMD_BAD_FORMAT
        if not verify_command(self.key, self.robot_id, c):
            return CMD_BAD_HMAC
        if c.nonce <= self.last_nonce:
            return CMD_REPLAY
        if self.ntp_synced and abs(c.nonce - now_ms) > self.cmd_max_skew_ms:
            return CMD_STALE

        self.last_nonce = c.nonce
        if c.cmd == CMD_LOCK:
            self.locked = True
            self._trip("LOCKED")
        else:
            # UNLOCK chỉ gỡ khóa từ xa; relay vẫn phải chờ SAFE đủ lâu mới đóng lại
            self.locked = False
        self.nvs.save(last_nonce=self.last_nonce, locked=self.locked)
        return CMD_OK

    def heartbeat(self, now_ms: int) -> Heartbeat:
        return build_heartbeat(self.key, self.robot_id, now_ms, int(self.locked), int(self.estop), self.cfg_hash)
