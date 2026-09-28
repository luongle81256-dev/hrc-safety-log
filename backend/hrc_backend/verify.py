"""Đối soát log off-chain với Merkle root trên chuỗi (dùng cho auditor, e2e, tamper demo).

- Tính lại root từ các bản ghi trong DB, so với root đã neo: KHỚP => mọi bản ghi của batch nguyên vẹn.
- Không khớp => batch bị sửa. Merkle chỉ cho biết "có sửa"; để CHỈ RA dòng bị sửa, kiểm lại HMAC của
  thiết bị trên từng dòng (kẻ sửa DB không có khóa thiết bị nên không ký lại được).
- Kiểm chứng thêm on-chain: gọi verifyRecord (contract tự tính leaf) cho 1 số bản ghi mẫu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hrc_common.merkle import build_layers, get_proof, telemetry_leaf
from hrc_common.protocol import verify_telemetry

from .chain import ChainClient
from .storage import LogStore


@dataclass
class BatchVerification:
    robot_id: int
    batch_id: int
    n_records: int
    root_local: str
    root_onchain: str
    onchain_checked: int = 0
    onchain_ok: int = 0
    hmac_bad_seqs: list[int] = field(default_factory=list)

    @property
    def root_match(self) -> bool:
        return self.root_local == self.root_onchain

    @property
    def ok(self) -> bool:
        return self.root_match and self.onchain_ok == self.onchain_checked and not self.hmac_bad_seqs


def verify_batch(store: LogStore, chain: ChainClient, key: bytes, robot_id: int, batch_id: int,
                 onchain_samples: int = 3) -> BatchVerification:
    recs = store.batch_records(robot_id, batch_id)
    root_onchain, *_ = chain.get_batch(robot_id, batch_id)
    if not recs:
        return BatchVerification(robot_id, batch_id, 0, "", root_onchain.hex())
    layers = build_layers([telemetry_leaf(t) for t in recs])
    res = BatchVerification(robot_id, batch_id, len(recs), layers[-1][0].hex(), root_onchain.hex())

    # Mẫu on-chain: đầu, giữa, cuối batch
    idx = sorted({0, len(recs) // 2, len(recs) - 1})[:onchain_samples]
    for i in idx:
        res.onchain_checked += 1
        res.onchain_ok += chain.verify_record(batch_id, recs[i].values(), get_proof(layers, i))

    if not res.root_match:  # khoanh vùng dòng bị sửa bằng HMAC thiết bị
        res.hmac_bad_seqs = [t.seq for t in recs if not verify_telemetry(key, t)]
    return res
