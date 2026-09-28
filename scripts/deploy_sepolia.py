"""Deploy HRCSafetyLog lên Sepolia bằng Hardhat Ignition (Phase 8).

Chuẩn bị: copy .env.sepolia.example -> .env.sepolia, điền SEPOLIA_RPC_URL + SEPOLIA_PRIVATE_KEY (ví TEST có SepoliaETH).
Chạy:
    python scripts/deploy_sepolia.py                 # deploy thật (hỏi xác nhận trước khi tiêu SepoliaETH)
    python scripts/deploy_sepolia.py --verify        # + verify mã nguồn trên Etherscan (cần ETHERSCAN_API_KEY)
    python scripts/deploy_sepolia.py --dry-run-local # CHẠY THỬ đúng đường Sepolia (mạng `sepolia`, 1 ví) trên Hardhat node local

Script: kiểm tra chain + số dư -> sinh file tham số vai trò -> `ignition deploy --network sepolia --parameters`
-> kiểm tra vai trò on-chain -> ghi CONTRACT_ADDRESS + CHAIN_START_BLOCK vào .env.sepolia -> in link Etherscan.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEPOLIA_CHAIN_ID = 11155111
CONTRACTS = ROOT / "contracts"
PARAMS_FILE = CONTRACTS / "ignition" / "parameters" / "sepolia.json"
ESTIMATED_GAS = 2_000_000  # deploy ~1.62M + 4 grantRole + registerRobot (đo ở Phase 1)
# Ví Hardhat #0–#4 (mnemonic "test test ... junk"): private key CÔNG KHAI trên mạng => cấm dùng trên testnet
HARDHAT_PUBLIC = {a.lower() for a in (
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC", "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
    "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65")}


def _env_key(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    return v or None


def _update_env_file(path: Path, values: dict[str, str]) -> None:
    """Ghi/cập nhật vài dòng KHÔNG bí mật (địa chỉ, block) vào .env.sepolia, giữ nguyên phần còn lại."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for k, v in values.items():
        line = f"{k}={v}"
        text = re.sub(rf"(?m)^{k}=.*$", line, text) if re.search(rf"(?m)^{k}=", text) else text + f"\n{line}\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="verify mã nguồn trên Etherscan (cần ETHERSCAN_API_KEY)")
    ap.add_argument("--redeploy", action="store_true", help="deploy contract MỚI dù đã có bản deploy trước (--reset)")
    ap.add_argument("--yes", action="store_true", help="không hỏi xác nhận")
    ap.add_argument("--dry-run-local", action="store_true",
                    help="chạy thử trên Hardhat node local bằng cấu hình mạng `sepolia` (không tiêu SepoliaETH)")
    args = ap.parse_args()

    if not args.dry_run_local:
        os.environ["HRC_NETWORK"] = "sepolia"
    from eth_account import Account
    from web3 import Web3

    from hrc_backend.chain import ChainClient, resolve_address
    from hrc_common.env import load_env

    try:
        load_env()
    except FileNotFoundError as e:
        print(f"[LỖI] {e}")
        return 2
    if args.dry_run_local:
        # Đúng đường Sepolia nhưng RPC = node local, ví deploy = Hardhat #0; vai trò = khóa local trong .env
        os.environ["SEPOLIA_RPC_URL"] = "http://127.0.0.1:8545"
        os.environ["SEPOLIA_PRIVATE_KEY"] = os.environ["ADMIN_PRIVATE_KEY"]
    rpc, deploy_key = _env_key("SEPOLIA_RPC_URL"), _env_key("SEPOLIA_PRIVATE_KEY")
    if not rpc or not deploy_key:
        print("[LỖI] Thiếu SEPOLIA_RPC_URL hoặc SEPOLIA_PRIVATE_KEY trong .env.sepolia")
        return 2

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print(f"[LỖI] Không kết nối được RPC (kiểm tra SEPOLIA_RPC_URL)")
        return 2
    chain_id = w3.eth.chain_id
    if chain_id != SEPOLIA_CHAIN_ID and not args.dry_run_local:
        print(f"[LỖI] RPC đang trỏ chain {chain_id}, không phải Sepolia ({SEPOLIA_CHAIN_ID})")
        return 2

    deployer = Account.from_key(deploy_key).address

    def role_addr(role: str) -> str:
        if a := _env_key(f"{role}_ADDRESS"):
            return Web3.to_checksum_address(a)
        if k := _env_key(f"{role}_PRIVATE_KEY"):
            return Account.from_key(k).address
        return deployer

    roles = {"device": Account.from_key(_env_key("DEVICE_PRIVATE_KEY") or deploy_key).address,
             "supervisor": role_addr("SUPERVISOR"), "operator": role_addr("OPERATOR"), "auditor": role_addr("AUDITOR")}
    balance = w3.eth.get_balance(deployer)
    cost = ESTIMATED_GAS * w3.eth.gas_price
    print(f"Mạng: chain {chain_id}{' (CHẠY THỬ trên node local)' if args.dry_run_local else ' (Sepolia)'}")
    print(f"Ví deploy (DEFAULT_ADMIN): {deployer} | số dư {Web3.from_wei(balance, 'ether'):.5f} ETH "
          f"| ước tính phí ~{Web3.from_wei(cost, 'ether'):.5f} ETH")
    for k, v in roles.items():
        print(f"  {k.upper():<10} -> {v}{'  (= ví deploy)' if v == deployer else ''}")
    public = [k.upper() for k, v in {"deploy": deployer, **roles}.items() if v.lower() in HARDHAT_PUBLIC]
    if public and not args.dry_run_local:
        print(f"[LỖI] Vai trò {', '.join(public)} đang là ví Hardhat mặc định – private key của nó CÔNG KHAI, "
              "ai cũng ký được (vd tự mở khóa robot). Nguyên nhân thường gặp: xóa mất dòng *_PRIVATE_KEY= trong "
              ".env.sepolia nên script lấy khóa local trong .env. Hãy giữ các dòng đó (để trống) hoặc điền ví test của nhóm.")
        return 2
    if len(set(roles.values()) | {deployer}) == 1:
        print("  [CHÚ Ý] Mọi vai trò dồn vào 1 ví – được cho demo, nhưng mất tách biệt trách nhiệm "
              "(nên đặt SUPERVISOR_ADDRESS = ví MetaMask khác)")
    if balance < cost:
        print("[LỖI] Số dư không đủ phí ước tính – xin thêm SepoliaETH từ faucet")
        return 2

    existing = CONTRACTS / "ignition" / "deployments" / f"chain-{chain_id}" / "deployed_addresses.json"
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    env = {**os.environ, "SEPOLIA_RPC_URL": rpc, "SEPOLIA_PRIVATE_KEY": deploy_key}
    if existing.exists() and not args.redeploy and not args.dry_run_local:
        print(f"[ĐÃ CÓ] Contract đã deploy: {resolve_address(chain_id)} – thêm --redeploy nếu muốn deploy bản mới")
        if not args.verify:
            return 0
        # Deploy đã xong nhưng verify lần trước lỗi (mạng, Etherscan chậm) => chỉ verify lại, không tiêu thêm ETH
        if not _env_key("ETHERSCAN_API_KEY"):
            print("[LỖI] --verify cần ETHERSCAN_API_KEY trong .env.sepolia")
            return 2
        print("Chỉ verify lại mã nguồn trên Etherscan (không deploy)...", flush=True)
        return subprocess.run([npx, "hardhat", "ignition", "verify", f"chain-{chain_id}", "--network", "sepolia"],
                              cwd=CONTRACTS, env=env, text=True, encoding="utf-8", errors="replace").returncode
    if not args.dry_run_local and not args.yes:
        if input("Deploy lên Sepolia (tiêu SepoliaETH)? Gõ 'yes' để tiếp tục: ").strip().lower() != "yes":
            print("Đã hủy")
            return 1

    params_file = (Path(os.getenv("TEMP", "/tmp")) / "hrc_sepolia_dryrun.json") if args.dry_run_local else PARAMS_FILE
    params_file.parent.mkdir(parents=True, exist_ok=True)
    params_file.write_text(json.dumps({"HRCSafetyLogModule": roles}, indent=2) + "\n", encoding="utf-8")

    cmd = [npx, "hardhat", "ignition", "deploy", "ignition/modules/HRCSafetyLog.ts", "--network", "sepolia",
           "--parameters", str(params_file)]
    if args.redeploy or args.dry_run_local:
        cmd.append("--reset")
    if args.verify and not args.dry_run_local:
        if not _env_key("ETHERSCAN_API_KEY"):
            print("[LỖI] --verify cần ETHERSCAN_API_KEY trong .env.sepolia")
            return 2
        cmd.append("--verify")
    print("Đang deploy (Sepolia ~12 s/khối, có thể mất 1–3 phút)...", flush=True)
    r = subprocess.run(cmd, cwd=CONTRACTS, env=env, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("[LỖI] Ignition deploy thất bại (xem log phía trên). Chạy lại lệnh này sẽ TIẾP TỤC từ bước dở dang.")
        return r.returncode

    address = resolve_address(chain_id)
    c = ChainClient(url=rpc, address=address)
    checks = {"DEVICE_ROLE": roles["device"], "SUPERVISOR_ROLE": roles["supervisor"],
              "OPERATOR_ROLE": roles["operator"], "AUDITOR_ROLE": roles["auditor"], "DEFAULT_ADMIN_ROLE": deployer}
    ok = all(c.has_role(role, a) for role, a in checks.items()) and c.robot_info(1).registered
    start = c.deploy_block()
    print(f"[{'OK' if ok else 'LỖI'}] Contract {address} tại block {start} – vai trò + robot 1 "
          f"{'đúng' if ok else 'SAI'} trên chuỗi")
    if not args.dry_run_local:
        _update_env_file(ROOT / ".env.sepolia", {"CONTRACT_ADDRESS": address, "CHAIN_START_BLOCK": str(start)})
        print("     Đã ghi CONTRACT_ADDRESS + CHAIN_START_BLOCK vào .env.sepolia")
        print(f"     Etherscan: https://sepolia.etherscan.io/address/{address}")
        print('     Tiếp theo: $env:HRC_NETWORK="sepolia"; hrc-backend   (xem README mục "Chạy trên Sepolia")')
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
