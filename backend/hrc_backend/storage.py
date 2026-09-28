"""Log off-chain bằng SQLite – "bản gốc" mà Merkle root trên chuỗi xác thực.

Bảng:
    telemetry      bản ghi đã qua kiểm tra (khóa chính robot_id+seq => DB tự chặn trùng)
    rejected       bản tin bị loại + lý do (bằng chứng tấn công/lỗi, phục vụ audit)
    seq_gaps       khoảng seq bị mất (MQTT QoS 0 mất gói)
    device_status  trạng thái online/heartbeat mới nhất của mỗi robot
    cmd_log        lệnh đã ký gửi xuống thiết bị (nonce tăng ngặt, bền qua restart)
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from hrc_common.protocol import Telemetry

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    robot_id       INTEGER NOT NULL,
    seq            INTEGER NOT NULL,
    ts_ms          INTEGER NOT NULL,
    distance_mm    INTEGER NOT NULL,
    v_robot_mmps   INTEGER NOT NULL,
    zone           INTEGER NOT NULL,
    estop          INTEGER NOT NULL,
    hmac           TEXT    NOT NULL,
    received_at_ms INTEGER NOT NULL,
    batch_id       INTEGER,            -- gán ở Phase 4 khi gom batch Merkle
    PRIMARY KEY (robot_id, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS rejected (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at_ms INTEGER NOT NULL,
    topic          TEXT    NOT NULL,
    reason         TEXT    NOT NULL,
    detail         TEXT    NOT NULL,
    robot_id       INTEGER,
    seq            INTEGER,
    payload        BLOB
);
CREATE INDEX IF NOT EXISTS idx_rejected_reason ON rejected(reason);

CREATE TABLE IF NOT EXISTS seq_gaps (
    robot_id       INTEGER NOT NULL,
    from_seq       INTEGER NOT NULL,
    to_seq         INTEGER NOT NULL,
    detected_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS device_status (
    robot_id            INTEGER PRIMARY KEY,
    online              INTEGER NOT NULL,
    last_hb_ts_ms       INTEGER,
    last_seen_at_ms     INTEGER,
    state               INTEGER,
    estop               INTEGER,
    cfg                 TEXT,
    cfg_match           INTEGER,
    offline_reason      TEXT,
    updated_at_ms       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cmd_log (
    robot_id    INTEGER NOT NULL,
    nonce       INTEGER NOT NULL,
    cmd         TEXT    NOT NULL,
    sent_at_ms  INTEGER NOT NULL,
    PRIMARY KEY (robot_id, nonce)
) WITHOUT ROWID;

-- Batch Merkle đã/đang neo lên chuỗi. PENDING được ghi TRƯỚC khi gửi tx => khôi phục được nếu sập giữa chừng.
CREATE TABLE IF NOT EXISTS batches (
    robot_id        INTEGER NOT NULL,
    batch_id        INTEGER NOT NULL,
    merkle_root     TEXT    NOT NULL,
    from_seq        INTEGER NOT NULL,
    to_seq          INTEGER NOT NULL,
    n_records       INTEGER NOT NULL,
    status          TEXT    NOT NULL,      -- PENDING | ANCHORED
    tx_hash         TEXT,
    block_number    INTEGER,
    gas_used        INTEGER,
    created_at_ms   INTEGER NOT NULL,
    anchored_at_ms  INTEGER,
    PRIMARY KEY (robot_id, batch_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id        INTEGER NOT NULL,
    kind            TEXT    NOT NULL,      -- INTRUSION | OVERSPEED | SENSOR_FAULT
    severity        INTEGER NOT NULL,
    operator        TEXT    NOT NULL,
    evidence_hash   TEXT    NOT NULL,      -- Merkle root của các bản ghi trong cửa sổ bằng chứng
    from_seq        INTEGER NOT NULL,
    to_seq          INTEGER NOT NULL,
    confidence      REAL    NOT NULL,
    status          TEXT    NOT NULL,      -- PENDING | SENT | FAILED
    onchain_id      INTEGER,
    tx_hash         TEXT,
    error           TEXT,
    created_at_ms   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
"""

ORPHAN_BATCH_ID = -1  # bản ghi không thể neo (seq <= toSeq đã có trên chuỗi, ví dụ DB bị xóa giữa chừng)


@dataclass(frozen=True, slots=True)
class BatchRow:
    robot_id: int
    batch_id: int
    merkle_root: str
    from_seq: int
    to_seq: int
    n_records: int
    status: str
    tx_hash: str | None
    block_number: int | None
    gas_used: int | None


@dataclass(frozen=True, slots=True)
class IncidentRow:
    id: int
    robot_id: int
    kind: str
    severity: int
    operator: str
    evidence_hash: str
    from_seq: int
    to_seq: int
    confidence: float
    status: str
    onchain_id: int | None
    tx_hash: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    robot_id: int
    online: bool
    last_hb_ts_ms: int | None
    last_seen_at_ms: int | None
    state: int | None
    estop: int | None
    cfg: str | None
    cfg_match: bool | None
    offline_reason: str | None


class LogStore:
    """Truy cập SQLite an toàn đa luồng (callback MQTT và luồng chính dùng chung)."""

    def __init__(self, path: Path | str, rejected_payload_max_bytes: int = 1024):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._payload_max = rejected_payload_max_bytes
        # Nạp seq cuối của mỗi robot => chống replay vẫn đúng sau khi backend khởi động lại
        self._last_seq: dict[int, int] = dict(
            self._db.execute("SELECT robot_id, MAX(seq) FROM telemetry GROUP BY robot_id").fetchall()
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------- telemetry ----------------
    def last_seq(self, robot_id: int) -> int | None:
        with self._lock:
            return self._last_seq.get(robot_id)

    def insert_telemetry(self, t: Telemetry, received_at_ms: int) -> bool:
        """Ghi bản ghi. Trả về False nếu (robot_id, seq) đã tồn tại."""
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO telemetry (robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop, hmac,"
                    " received_at_ms) VALUES (?,?,?,?,?,?,?,?,?)",
                    (*t.values(), t.hmac, received_at_ms),
                )
            except sqlite3.IntegrityError:
                return False
            self._last_seq[t.robot_id] = max(t.seq, self._last_seq.get(t.robot_id, t.seq))
            return True

    def telemetry(self, robot_id: int, from_seq: int = 0, to_seq: int | None = None) -> list[Telemetry]:
        with self._lock:
            rows = self._db.execute(
                "SELECT robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop, hmac FROM telemetry"
                " WHERE robot_id = ? AND seq >= ? AND seq <= ? ORDER BY seq",
                (robot_id, from_seq, to_seq if to_seq is not None else 2**63 - 1),
            ).fetchall()
        return [Telemetry(*r) for r in rows]

    def count_telemetry(self, robot_id: int | None = None) -> int:
        with self._lock:
            if robot_id is None:
                return self._db.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
            return self._db.execute("SELECT COUNT(*) FROM telemetry WHERE robot_id = ?", (robot_id,)).fetchone()[0]

    # ---------------- bản tin bị loại / gap ----------------
    def insert_rejection(self, received_at_ms: int, topic: str, reason: str, detail: str,
                         payload: bytes, robot_id: int | None = None, seq: int | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO rejected (received_at_ms, topic, reason, detail, robot_id, seq, payload)"
                " VALUES (?,?,?,?,?,?,?)",
                (received_at_ms, topic[:256], reason, detail[:512], robot_id, seq, bytes(payload[: self._payload_max])),
            )

    def rejection_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._db.execute("SELECT reason, COUNT(*) FROM rejected GROUP BY reason").fetchall())

    def insert_gap(self, robot_id: int, from_seq: int, to_seq: int, detected_at_ms: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO seq_gaps VALUES (?,?,?,?)", (robot_id, from_seq, to_seq, detected_at_ms))

    def gaps(self, robot_id: int) -> list[tuple[int, int]]:
        with self._lock:
            return self._db.execute(
                "SELECT from_seq, to_seq FROM seq_gaps WHERE robot_id = ? ORDER BY from_seq", (robot_id,)
            ).fetchall()

    # ---------------- trạng thái thiết bị ----------------
    def upsert_device_status(self, st: DeviceStatus, now_ms: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO device_status VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(robot_id) DO UPDATE SET"
                " online=excluded.online, last_hb_ts_ms=excluded.last_hb_ts_ms,"
                " last_seen_at_ms=excluded.last_seen_at_ms, state=excluded.state, estop=excluded.estop,"
                " cfg=excluded.cfg, cfg_match=excluded.cfg_match, offline_reason=excluded.offline_reason,"
                " updated_at_ms=excluded.updated_at_ms",
                (st.robot_id, int(st.online), st.last_hb_ts_ms, st.last_seen_at_ms, st.state, st.estop, st.cfg,
                 None if st.cfg_match is None else int(st.cfg_match), st.offline_reason, now_ms),
            )

    def device_statuses(self) -> dict[int, DeviceStatus]:
        with self._lock:
            rows = self._db.execute(
                "SELECT robot_id, online, last_hb_ts_ms, last_seen_at_ms, state, estop, cfg, cfg_match,"
                " offline_reason FROM device_status"
            ).fetchall()
        return {
            r[0]: DeviceStatus(r[0], bool(r[1]), r[2], r[3], r[4], r[5], r[6],
                               None if r[7] is None else bool(r[7]), r[8])
            for r in rows
        }

    # ---------------- batch Merkle ----------------
    def unbatched(self, robot_id: int, limit: int) -> list[tuple[Telemetry, int]]:
        """Bản ghi chưa thuộc batch nào (theo seq tăng dần) kèm received_at_ms."""
        with self._lock:
            rows = self._db.execute(
                "SELECT robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop, hmac, received_at_ms"
                " FROM telemetry WHERE robot_id = ? AND batch_id IS NULL ORDER BY seq LIMIT ?",
                (robot_id, limit),
            ).fetchall()
        return [(Telemetry(*r[:8]), r[8]) for r in rows]

    def mark_orphans(self, robot_id: int, max_seq: int) -> int:
        """Đánh dấu bản ghi chưa neo có seq <= max_seq là không thể neo (contract sẽ từ chối SeqOverlap)."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE telemetry SET batch_id = ? WHERE robot_id = ? AND batch_id IS NULL AND seq <= ?",
                (ORPHAN_BATCH_ID, robot_id, max_seq),
            )
            return cur.rowcount

    def create_batch(self, robot_id: int, batch_id: int, root_hex: str, from_seq: int, to_seq: int,
                     n_records: int, now_ms: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO batches (robot_id, batch_id, merkle_root, from_seq, to_seq, n_records, status,"
                " created_at_ms) VALUES (?,?,?,?,?,?,'PENDING',?)",
                (robot_id, batch_id, root_hex, from_seq, to_seq, n_records, now_ms),
            )

    def confirm_batch(self, robot_id: int, batch_id: int, tx_hash: str | None, block_number: int | None,
                      gas_used: int | None, now_ms: int) -> None:
        """Đánh dấu ANCHORED và gán batch_id cho đúng các bản ghi thuộc batch (1 giao dịch SQLite)."""
        with self._lock:
            b = self._db.execute("SELECT from_seq, to_seq FROM batches WHERE robot_id=? AND batch_id=?",
                                 (robot_id, batch_id)).fetchone()
            self._db.execute("BEGIN")
            try:
                self._db.execute(
                    "UPDATE batches SET status='ANCHORED', tx_hash=?, block_number=?, gas_used=?, anchored_at_ms=?"
                    " WHERE robot_id=? AND batch_id=?",
                    (tx_hash, block_number, gas_used, now_ms, robot_id, batch_id),
                )
                self._db.execute(
                    "UPDATE telemetry SET batch_id=? WHERE robot_id=? AND seq BETWEEN ? AND ? AND batch_id IS NULL",
                    (batch_id, robot_id, b[0], b[1]),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def delete_batch(self, robot_id: int, batch_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM batches WHERE robot_id=? AND batch_id=? AND status='PENDING'",
                             (robot_id, batch_id))

    def batches(self, robot_id: int | None = None, status: str | None = None) -> list[BatchRow]:
        q, args = "SELECT robot_id, batch_id, merkle_root, from_seq, to_seq, n_records, status, tx_hash," \
                  " block_number, gas_used FROM batches WHERE 1=1", []
        if robot_id is not None:
            q, args = q + " AND robot_id = ?", args + [robot_id]
        if status is not None:
            q, args = q + " AND status = ?", args + [status]
        with self._lock:
            return [BatchRow(*r) for r in self._db.execute(q + " ORDER BY robot_id, batch_id", args).fetchall()]

    def batch_records(self, robot_id: int, batch_id: int) -> list[Telemetry]:
        with self._lock:
            rows = self._db.execute(
                "SELECT robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop, hmac FROM telemetry"
                " WHERE robot_id = ? AND batch_id = ? ORDER BY seq", (robot_id, batch_id),
            ).fetchall()
        return [Telemetry(*r) for r in rows]

    # ---------------- sự cố ----------------
    def create_incident(self, robot_id: int, kind: str, severity: int, operator: str, evidence_hash: str,
                        from_seq: int, to_seq: int, confidence: float, now_ms: int) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO incidents (robot_id, kind, severity, operator, evidence_hash, from_seq, to_seq,"
                " confidence, status, created_at_ms) VALUES (?,?,?,?,?,?,?,?,'PENDING',?)",
                (robot_id, kind, severity, operator, evidence_hash, from_seq, to_seq, confidence, now_ms),
            )
            return cur.lastrowid

    def update_incident(self, local_id: int, status: str, onchain_id: int | None = None,
                        tx_hash: str | None = None, error: str | None = None, operator: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE incidents SET status=?, onchain_id=?, tx_hash=?, error=?, operator=COALESCE(?, operator)"
                " WHERE id=?", (status, onchain_id, tx_hash, error, operator, local_id))

    def incidents(self, robot_id: int | None = None) -> list[IncidentRow]:
        q = "SELECT id, robot_id, kind, severity, operator, evidence_hash, from_seq, to_seq, confidence, status," \
            " onchain_id, tx_hash, error FROM incidents"
        with self._lock:
            rows = (self._db.execute(q + " WHERE robot_id = ? ORDER BY id", (robot_id,)) if robot_id is not None
                    else self._db.execute(q + " ORDER BY id")).fetchall()
        return [IncidentRow(*r) for r in rows]

    # ---------------- key-value (vị trí đọc event...) ----------------
    def kv_get(self, key: str) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, value))

    # ---------------- lệnh ----------------
    def next_cmd_nonce(self, robot_id: int, now_ms: int, cmd: str) -> int:
        """Cấp nonce = max(now_ms, nonce_cũ + 1) và ghi nhật ký (tăng ngặt kể cả khi đồng hồ lùi)."""
        with self._lock:
            last = self._db.execute("SELECT MAX(nonce) FROM cmd_log WHERE robot_id = ?", (robot_id,)).fetchone()[0]
            nonce = max(now_ms, (last or 0) + 1)
            self._db.execute("INSERT INTO cmd_log VALUES (?,?,?,?)", (robot_id, nonce, cmd, now_ms))
            return nonce
