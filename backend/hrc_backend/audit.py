"""Nghiệp vụ auditor: đối soát toàn bộ log với chuỗi, ghi kết quả lên chuỗi, và mô phỏng sửa log (tamper).

Dùng bởi scripts/verify_log.py, scripts/tamper_demo.py, scripts/demo_scenario.py.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from web3 import Web3

from .chain import ChainClient, TxResult
from .storage import LogStore
from .verify import BatchVerification, verify_batch

ZONE_NAMES = ("SAFE", "WARNING", "DANGER")


def verify_all(store: LogStore, chain: ChainClient, key: bytes, robot_id: int,
               onchain_samples: int = 3) -> list[BatchVerification]:
    """Đối soát MỌI batch đã neo của robot (root off-chain vs on-chain + verifyRecord mẫu + HMAC khi lệch)."""
    return [verify_batch(store, chain, key, robot_id, b.batch_id, onchain_samples)
            for b in store.batches(robot_id, "ANCHORED")]


def report_hash(v: BatchVerification) -> bytes:
    """Hash báo cáo đối soát (báo cáo lưu off-chain, hash ghi on-chain qua recordAudit)."""
    body = json.dumps({**asdict(v), "root_match": v.root_match, "ok": v.ok}, sort_keys=True).encode()
    return Web3.keccak(body)


def record_audit(auditor: ChainClient, v: BatchVerification) -> TxResult:
    return auditor.record_audit(v.robot_id, v.batch_id, v.ok, report_hash(v))


@dataclass
class TamperedRow:
    robot_id: int
    batch_id: int
    seq: int
    field: str
    old: int
    new: int
    zone_before: int


def pick_victim(store: LogStore, robot_id: int, batch_id: int) -> int:
    """Chọn bản ghi để sửa: ưu tiên bản ghi DANGER (kịch bản 'xóa dấu vết sự cố'), không có thì lấy giữa batch."""
    recs = store.batch_records(robot_id, batch_id)
    if not recs:
        raise ValueError(f"batch {robot_id}/{batch_id} không có bản ghi")
    danger = [t for t in recs if t.zone == 2]
    return (danger[0] if danger else recs[len(recs) // 2]).seq


def tamper(store: LogStore, robot_id: int, batch_id: int, seq: int) -> TamperedRow:
    """Giả lập người có quyền ghi DB sửa 1 dòng log: bản ghi DANGER -> SAFE + khoảng cách xa (che giấu sự cố)."""
    row = store._db.execute("SELECT distance_mm, zone FROM telemetry WHERE robot_id=? AND seq=? AND batch_id=?",
                            (robot_id, seq, batch_id)).fetchone()
    if row is None:
        raise ValueError(f"không có bản ghi seq {seq} trong batch {robot_id}/{batch_id}")
    old_d, old_zone = row
    new_d = old_d + 700 if old_d < 8190 else 900
    with store._lock:
        store._db.execute("UPDATE telemetry SET distance_mm=?, zone=0 WHERE robot_id=? AND seq=?", (new_d, robot_id, seq))
    return TamperedRow(robot_id, batch_id, seq, "distance_mm", old_d, new_d, old_zone)


def restore(store: LogStore, t: TamperedRow) -> None:
    with store._lock:
        store._db.execute("UPDATE telemetry SET distance_mm=?, zone=? WHERE robot_id=? AND seq=?",
                          (t.old, t.zone_before, t.robot_id, t.seq))
