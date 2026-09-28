"""Công cụ AUDITOR: đối soát toàn bộ log off-chain với Merkle root trên chuỗi.

Chạy:
    python scripts/verify_log.py                         # mạng local, DB theo SQLITE_PATH
    python scripts/verify_log.py --db backend/data/x.sqlite3
    python scripts/verify_log.py --record-audit          # + AUDITOR ghi kết quả từng batch lên chuỗi (recordAudit)
    $env:HRC_NETWORK="sepolia"; python scripts/verify_log.py   # trên Sepolia
Mã thoát 0 = mọi batch khớp; 1 = có batch bị sửa.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from hrc_backend.audit import record_audit, verify_all  # noqa: E402
from hrc_backend.chain import ChainClient, ChainError  # noqa: E402
from hrc_backend.settings import hmac_key, open_store  # noqa: E402

EXPLORERS = {11155111: "https://sepolia.etherscan.io"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--robot", type=int, default=1)
    ap.add_argument("--record-audit", action="store_true", help="ghi kết quả lên chuỗi bằng AUDITOR_PRIVATE_KEY")
    args = ap.parse_args()
    try:
        key = hmac_key()
        chain = ChainClient()
        auditor = ChainClient.from_env("AUDITOR") if args.record_audit else None
    except (ValueError, ChainError) as e:
        print(f"[LỖI] {e}")
        return 2
    store = open_store(args.db)
    try:
        results = verify_all(store, chain, key, args.robot)
        print(f"Đối soát robot {args.robot} – contract {chain.address} (chain {chain.chain_id}), {len(results)} batch")
        print(f"  {'batch':>6} {'bản ghi':>8}  {'root':<8} {'verifyRecord':>13}  kết quả")
        bad = 0
        for v in results:
            status = "✓ nguyên vẹn" if v.ok else f"✗ BỊ SỬA{' – seq ' + ', '.join(map(str, v.hmac_bad_seqs)) if v.hmac_bad_seqs else ''}"
            bad += not v.ok
            line = (f"  {v.batch_id:>6} {v.n_records:>8}  {'khớp' if v.root_match else 'LỆCH':<8} "
                    f"{v.onchain_ok:>6}/{v.onchain_checked:<6}  {status}")
            if auditor:
                tx = record_audit(auditor, v)
                ex = EXPLORERS.get(chain.chain_id)
                line += f"  | recordAudit {ex + '/tx/' + tx.tx_hash if ex else tx.tx_hash[:14]}"
            print(line)
        print(f"KẾT QUẢ: {len(results) - bad}/{len(results)} batch nguyên vẹn" + (f", {bad} batch BỊ SỬA" if bad else ""))
        return 1 if bad else 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
