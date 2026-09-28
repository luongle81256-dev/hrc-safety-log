"""TÍNH CHI PHÍ THẬT của một buổi chạy (mạch thật hoặc mô phỏng) từ các giao dịch đã nằm trên chuỗi.

Đọc mọi giao dịch gửi tới contract HRCSafetyLog kể từ lúc deploy, nhóm theo hàm (anchorLog, reportIncident,
unlock…), cộng gas đã dùng, rồi quy ra ETH (và USD nếu nhập giá ETH). Chỉ ĐỌC chuỗi, không gửi giao dịch nào,
nên chạy được cả lúc đang demo.

Chạy (blockchain Hardhat phải đang chạy, sau hoặc trong buổi demo):
    python scripts/chi_phi_gas.py                        # giá gas giả định 1 gwei và 10 gwei
    python scripts/chi_phi_gas.py --gwei 0.5 3 --eth-usd 2500
    $env:HRC_NETWORK="sepolia"; python scripts/chi_phi_gas.py   # đọc chi phí thật trên Sepolia
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from hrc_backend.chain import ChainClient, ChainError  # noqa: E402

SHIFT_HOURS = 8
MIN_MINUTES = 3  # ngắn hơn thì không ngoại suy theo giờ (sai số quá lớn)
# Giao dịch chỉ làm 1 lần khi triển khai (không tính vào chi phí vận hành)
SETUP_FNS = {"deploy (tạo contract)", "grantRole", "revokeRole", "renounceRole", "registerRobot",
             "setRobotDevice", "resetViolations"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gwei", type=float, nargs="+", default=[1.0, 10.0], help="giá gas giả định (gwei) để quy ra ETH")
    ap.add_argument("--eth-usd", type=float, default=0.0, help="giá 1 ETH (USD) để quy ra tiền; 0 = bỏ qua")
    ap.add_argument("--from-block", type=int, help="bắt đầu từ block này (mặc định: block deploy contract)")
    args = ap.parse_args()

    try:
        c = ChainClient()
    except ChainError as e:
        print(f"[LỖI] {e}\n=> Blockchain (npx hardhat node) đã chạy và contract đã deploy chưa?")
        return 2
    w3, addr = c.w3, c.address.lower()
    start = args.from_block if args.from_block is not None else c.deploy_block()
    end = w3.eth.block_number

    by_fn: dict[str, list[int]] = defaultdict(list)  # tên hàm -> [gas của từng giao dịch]
    failed = 0
    t_first = t_last = None  # thời điểm giao dịch VẬN HÀNH đầu/cuối
    for n in range(start, end + 1):
        blk = w3.eth.get_block(n, full_transactions=True)
        for tx in blk.transactions:
            to = (tx.get("to") or "").lower()
            if to != addr and to != "":
                continue
            rc = w3.eth.get_transaction_receipt(tx["hash"])
            if to == "":  # giao dịch tạo contract: chỉ tính nếu tạo ra ĐÚNG contract này
                if (rc.contractAddress or "").lower() != addr:
                    continue
                name = "deploy (tạo contract)"
            else:
                try:
                    name = c.contract.decode_function_input(tx["input"])[0].fn_name
                except ValueError:
                    name = "(không rõ)"
            if rc.status != 1:
                failed += 1
                continue
            by_fn[name].append(rc.gasUsed)
            if name not in SETUP_FNS:
                t_first = blk.timestamp if t_first is None else t_first
                t_last = blk.timestamp

    if not by_fn:
        print(f"Chưa có giao dịch nào tới contract {c.address} (block {start}..{end}). Chạy demo một lúc rồi thử lại.")
        return 1

    def table(title: str, names: list[str]) -> int:
        rows = [(n, by_fn[n]) for n in names]
        total = sum(sum(g) for _, g in rows)
        print(f"\n{title}")
        print("-" * 70)
        print(f" {'Hàm':<24}{'Số lần':>8}{'Gas TB/lần':>14}{'Tổng gas':>16}")
        print("-" * 70)
        for n, g in sorted(rows, key=lambda r: -sum(r[1])):
            print(f" {n:<24}{len(g):>8}{sum(g) // len(g):>14,}{sum(g):>16,}")
        print("-" * 70)
        print(f" {'TỔNG':<24}{sum(len(g) for _, g in rows):>8}{'':>14}{total:>16,}")
        return total

    print(f"Contract {c.address} trên chain {c.chain_id}, block {start}..{end}"
          + (f" | {failed} giao dịch lỗi (bỏ qua)" if failed else ""))
    setup = [n for n in by_fn if n in SETUP_FNS]
    ops = [n for n in by_fn if n not in SETUP_FNS]
    setup_gas = table("A. CHI PHÍ TRIỂN KHAI (chỉ làm 1 lần)", setup) if setup else 0
    if not ops:
        print("\nChưa có giao dịch vận hành (anchorLog, reportIncident, unlock…). Chạy demo một lúc rồi thử lại.")
        return 1
    ops_gas = table("B. CHI PHÍ VẬN HÀNH (buổi chạy này)", ops)
    minutes = (t_last - t_first) / 60
    print(f" Thời gian vận hành: {minutes:.1f} phút")

    eth = lambda gas, g: gas * g * 1e-9  # noqa: E731
    usd = args.eth_usd > 0
    fmt_usd = lambda e: f"  (~{e * args.eth_usd:,.2f} USD)" if usd else ""  # noqa: E731
    print("\nC. QUY RA TIỀN (gas × giá gas; 1 gwei = 0,000000001 ETH)")
    for g in args.gwei:
        print(f" @ {g:g} gwei: triển khai {eth(setup_gas, g):.6f} ETH{fmt_usd(eth(setup_gas, g))} | "
              f"buổi này {eth(ops_gas, g):.6f} ETH{fmt_usd(eth(ops_gas, g))}")
    if minutes < MIN_MINUTES:
        print(f"\n Buổi chạy mới {minutes:.1f} phút – quá ngắn để ước tính theo giờ. Chạy demo ≥ {MIN_MINUTES} phút rồi thử lại.")
    else:
        per_hour = ops_gas / minutes * 60
        print(f"\nD. ƯỚC TÍNH NẾU CHẠY LIÊN TỤC (theo tốc độ buổi này: {per_hour:,.0f} gas/giờ)")
        for g in args.gwei:
            print(f" @ {g:g} gwei: 1 giờ {eth(per_hour, g):.5f} ETH{fmt_usd(eth(per_hour, g))} | "
                  f"1 ca {SHIFT_HOURS} giờ {eth(per_hour * SHIFT_HOURS, g):.4f} ETH{fmt_usd(eth(per_hour * SHIFT_HOURS, g))}")
    print("\nLưu ý:")
    print(" - Gas là số THẬT đo từ chuỗi; giá gas và giá ETH là GIẢ ĐỊNH (thay đổi theo thị trường).")
    print(" - Trên Hardhat local và Sepolia, ETH không có giá trị thật: con số trên là ước tính nếu chạy trên Ethereum.")
    print(" - Demo thường chạy --batch-interval-ms 8000 (8 s/batch); cấu hình mặc định 30 s thì anchorLog ít hơn ~3,5 lần.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
