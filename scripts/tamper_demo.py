"""DEMO "SỬA 1 DÒNG LOG → BỊ PHÁT HIỆN" (màn thuyết phục nhất của đồ án).

Kịch bản: người có quyền ghi database (vd quản trị hệ thống) sửa 1 bản ghi DANGER thành SAFE để che giấu sự cố.
    1. Đối soát TRƯỚC khi sửa  -> khớp Merkle root trên chuỗi
    2. Sửa 1 dòng trong SQLite
    3. Đối soát SAU khi sửa    -> root lệch => chứng minh log đã bị sửa; HMAC thiết bị chỉ ra đúng dòng
    4. (tùy chọn) AUDITOR ghi kết quả "KHÔNG khớp" lên chuỗi (recordAudit) – bằng chứng không chối được
    5. Khôi phục dòng đã sửa (trừ khi --keep)

Chạy:  python scripts/tamper_demo.py [--batch N] [--seq S] [--record-audit] [--keep]
       (backend có thể đang chạy; trên Sepolia: $env:HRC_NETWORK="sepolia")
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from hrc_backend.audit import ZONE_NAMES, pick_victim, record_audit, restore, tamper  # noqa: E402
from hrc_backend.chain import ChainClient, ChainError  # noqa: E402
from hrc_backend.settings import hmac_key, open_store  # noqa: E402
from hrc_backend.verify import verify_batch  # noqa: E402

EXPLORERS = {11155111: "https://sepolia.etherscan.io"}


def _show(v) -> None:
    print(f"   root off-chain : 0x{v.root_local[:16]}…")
    print(f"   root on-chain  : 0x{v.root_onchain[:16]}…   => {'KHỚP' if v.root_match else 'LỆCH !!!'}")
    print(f"   verifyRecord on-chain (contract tự tính leaf): {v.onchain_ok}/{v.onchain_checked} bản ghi mẫu hợp lệ")
    if v.hmac_bad_seqs:
        print(f"   HMAC thiết bị không khớp ở seq: {v.hmac_bad_seqs}  <= dòng bị sửa")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--robot", type=int, default=1)
    ap.add_argument("--batch", type=int, help="batch cần sửa (mặc định: batch mới nhất có bản ghi DANGER, không có thì mới nhất)")
    ap.add_argument("--seq", type=int, help="seq cần sửa (mặc định: bản ghi DANGER đầu tiên của batch)")
    ap.add_argument("--record-audit", action="store_true", help="AUDITOR ghi kết quả lên chuỗi")
    ap.add_argument("--keep", action="store_true", help="KHÔNG khôi phục dòng đã sửa")
    args = ap.parse_args()
    try:
        key, chain = hmac_key(), ChainClient()
    except (ValueError, ChainError) as e:
        print(f"[LỖI] {e}")
        return 2
    store = open_store(args.db)
    try:
        batches = store.batches(args.robot, "ANCHORED")
        if not batches:
            print("[LỖI] Chưa có batch nào được neo – chạy hrc-backend + hrc-sim một lúc trước")
            return 2
        if args.batch:
            b = next(x for x in batches if x.batch_id == args.batch)
        else:
            with_danger = [x for x in batches if any(t.zone == 2 for t in store.batch_records(args.robot, x.batch_id))]
            b = (with_danger or batches)[-1]
        seq = args.seq or pick_victim(store, args.robot, b.batch_id)
        ex = EXPLORERS.get(chain.chain_id)
        print(f"Batch #{b.batch_id} robot {args.robot}: {b.n_records} bản ghi (seq {b.from_seq}–{b.to_seq}), "
              f"neo bởi tx {ex + '/tx/' + b.tx_hash if ex and b.tx_hash else b.tx_hash}")

        print("\n1) ĐỐI SOÁT TRƯỚC KHI SỬA")
        before = verify_batch(store, chain, key, args.robot, b.batch_id)
        _show(before)

        t = tamper(store, args.robot, b.batch_id, seq)
        print(f"\n2) SỬA LOG: seq {seq}: distance {t.old} -> {t.new} mm, zone {ZONE_NAMES[t.zone_before]} -> SAFE "
              f"(giả làm 'không có ai trong vùng nguy hiểm')")

        print("\n3) ĐỐI SOÁT SAU KHI SỬA")
        after = verify_batch(store, chain, key, args.robot, b.batch_id)
        _show(after)
        detected = before.ok and not after.root_match and after.hmac_bad_seqs == [seq]
        print(f"\n   => {'PHÁT HIỆN log bị sửa và chỉ đúng dòng bị sửa' if detected else 'KẾT QUẢ BẤT THƯỜNG – kiểm tra lại'}")

        if args.record_audit:
            auditor = ChainClient.from_env("AUDITOR")
            tx = record_audit(auditor, after)
            print(f"\n4) AUDITOR ghi lên chuỗi: recordAudit(batch {b.batch_id}, passed=false) – "
                  f"{ex + '/tx/' + tx.tx_hash if ex else tx.tx_hash}")
        if not args.keep:
            restore(store, t)
            ok = verify_batch(store, chain, key, args.robot, b.batch_id).ok
            print(f"\n5) Đã khôi phục dòng seq {seq} – đối soát lại: {'KHỚP' if ok else 'VẪN LỆCH'}")
        return 0 if detected else 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
