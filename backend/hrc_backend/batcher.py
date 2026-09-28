"""Gom log off-chain thành batch -> Merkle root -> anchorLog.

Batch đóng khi đủ batch_max_records HOẶC bản ghi cũ nhất đã chờ quá batch_max_interval_ms.
Khôi phục khi sập: batch được ghi PENDING TRƯỚC khi gửi tx; lúc khởi động recover() đối chiếu với
chuỗi – root trên chuỗi khớp => xác nhận, không khớp/không có => xóa PENDING để gom lại.
batchId và dải seq luôn lấy từ CHUỖI (nguồn sự thật), không tin bộ đếm cục bộ.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from hrc_common.config import load_config
from hrc_common.merkle import merkle_root, telemetry_leaf

from .chain import ChainClient, ChainError
from .storage import LogStore

log = logging.getLogger("hrc-batcher")


@dataclass(frozen=True, slots=True)
class AnchoredBatch:
    robot_id: int
    batch_id: int
    root_hex: str
    from_seq: int
    to_seq: int
    n_records: int
    tx_hash: str
    gas_used: int


class MerkleBatcher:
    def __init__(self, store: LogStore, clock: Callable[[], int], max_records: int | None = None,
                 max_interval_ms: int | None = None):
        cfg = load_config()
        self.store = store
        self.clock = clock
        self.robot_ids = list(cfg["system"]["robot_ids"])
        self.max_records = max_records or cfg["merkle"]["batch_max_records"]
        self.max_interval_ms = max_interval_ms if max_interval_ms is not None else cfg["merkle"]["batch_max_interval_ms"]

    def recover(self, chain: ChainClient) -> None:
        """Xử lý batch PENDING còn sót từ lần chạy trước."""
        for b in self.store.batches(status="PENDING"):
            root, *_ = chain.get_batch(b.robot_id, b.batch_id)
            if root.hex() == b.merkle_root:
                self.store.confirm_batch(b.robot_id, b.batch_id, None, None, None, self.clock())
                log.info("Khôi phục: batch %d/%d đã có trên chuỗi => xác nhận", b.robot_id, b.batch_id)
            else:
                self.store.delete_batch(b.robot_id, b.batch_id)
                log.warning("Khôi phục: batch %d/%d chưa lên chuỗi => bỏ PENDING, gom lại", b.robot_id, b.batch_id)

    def run_once(self, chain: ChainClient, force: bool = False) -> list[AnchoredBatch]:
        """Neo mọi batch đến hạn (force=True: neo ngay mọi bản ghi chưa neo, dùng khi dừng/demo)."""
        done = []
        for robot_id in self.robot_ids:
            while True:  # có thể tồn đọng nhiều batch đầy
                b = self._anchor_robot(chain, robot_id, force)
                if b is None:
                    break
                done.append(b)
        return done

    def _anchor_robot(self, chain: ChainClient, robot_id: int, force: bool) -> AnchoredBatch | None:
        rows = self.store.unbatched(robot_id, self.max_records)
        if not rows:
            return None
        now = self.clock()
        if not force and len(rows) < self.max_records and now - rows[0][1] < self.max_interval_ms:
            return None

        info = chain.robot_info(robot_id)
        if not info.registered:
            log.error("Robot %d chưa đăng ký trên contract – không neo được", robot_id)
            return None
        if info.last_batch_id > 0:
            orphans = self.store.mark_orphans(robot_id, info.last_to_seq)
            if orphans:
                log.error("Robot %d: %d bản ghi có seq <= %d (đã có trên chuỗi) – không thể neo, đánh dấu orphan",
                          robot_id, orphans, info.last_to_seq)
                rows = self.store.unbatched(robot_id, self.max_records)
                if not rows:
                    return None

        recs = [t for t, _ in rows]
        root = merkle_root([telemetry_leaf(t) for t in recs])
        batch_id = info.last_batch_id + 1
        from_seq, to_seq = recs[0].seq, recs[-1].seq
        self.store.create_batch(robot_id, batch_id, root.hex(), from_seq, to_seq, len(recs), now)
        try:
            tx = chain.anchor_log(robot_id, batch_id, root, from_seq, to_seq)
        except ChainError as e:
            self.store.delete_batch(robot_id, batch_id)
            log.error("anchorLog robot %d batch %d thất bại: %s", robot_id, batch_id, e)
            return None
        self.store.confirm_batch(robot_id, batch_id, tx.tx_hash, tx.block_number, tx.gas_used, self.clock())
        log.info("NEO batch robot %d #%d: %d bản ghi (seq %d..%d) root=0x%s… gas=%d tx=%s",
                 robot_id, batch_id, len(recs), from_seq, to_seq, root.hex()[:12], tx.gas_used, tx.tx_hash[:12])
        return AnchoredBatch(robot_id, batch_id, root.hex(), from_seq, to_seq, len(recs), tx.tx_hash, tx.gas_used)
