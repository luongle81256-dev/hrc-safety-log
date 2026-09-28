"""KỊCH BẢN DEMO BẢO VỆ – chạy trên hệ thống ĐANG MỞ (backend + dashboard), có lời dẫn từng bước.

Chuẩn bị (mỗi lệnh 1 terminal):
    cd contracts; npx hardhat node          + ignition deploy --network localhost --reset   (local)
    hrc-backend                              (Sepolia: $env:HRC_NETWORK="sepolia"; hrc-backend)
    cd dashboard; npm run dev                -> mở http://localhost:5173, kết nối MetaMask (ví SUPERVISOR)
Chạy:
    python scripts/demo_scenario.py                  # mở khóa: chờ nhóm bấm trên dashboard (MetaMask)
    python scripts/demo_scenario.py --auto-unlock    # script tự mở khóa bằng SUPERVISOR_PRIVATE_KEY
    python scripts/demo_scenario.py --pause          # dừng chờ Enter giữa các bước (vừa nói vừa demo)

Các bước:
  1. Robot chạy bình thường (SAFE, relay đóng, log được neo theo batch)
  2. Người vào vùng nguy hiểm -> FAST LANE ngắt relay NGAY tại thiết bị; AI xác nhận -> reportIncident -> LOCKED
  3. Thử cho chạy lại -> contract TỪ CHỐI (operator không check-in được robot đang khóa)
  4. SUPERVISOR ký unlock() bằng MetaMask -> event Unlocked -> backend gửi UNLOCK có ký tới thiết bị
  5. Sửa 1 dòng log off-chain -> đối soát với Merkle root on-chain phát hiện ngay -> khôi phục
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from hrc_backend.audit import pick_victim, restore, tamper  # noqa: E402
from hrc_backend.chain import STATE_LOCKED, ChainClient, ChainError  # noqa: E402
from hrc_backend.settings import hmac_key, open_store  # noqa: E402
from hrc_backend.verify import verify_batch  # noqa: E402
from hrc_sim import cli as sim_cli  # noqa: E402

EXPLORERS = {11155111: "https://sepolia.etherscan.io"}
R = 1


class Demo:
    def __init__(self, args):
        self.args = args
        self.api = f"http://{os.getenv('BACKEND_HOST', '127.0.0.1')}:{os.getenv('BACKEND_PORT', '8000')}"
        self.chain = ChainClient()
        self.explorer = EXPLORERS.get(self.chain.chain_id)
        self.sim: threading.Thread | None = None

    # ---------------- tiện ích ----------------
    def step(self, n: int, title: str) -> None:
        print(f"\n{'=' * 78}\n BƯỚC {n}. {title}\n{'=' * 78}", flush=True)
        if self.args.pause:
            input("   (Enter để tiếp tục) ")

    def say(self, text: str) -> None:
        print(f"   {text}", flush=True)

    def link(self, tx: str) -> str:
        return f"{self.explorer}/tx/{tx}" if self.explorer and tx else (tx or "")

    def state(self) -> dict:
        with urllib.request.urlopen(f"{self.api}/api/state", timeout=5) as r:
            return json.load(r)["robots"][0]

    def wait(self, cond, timeout: float, what: str) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if cond():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        self.say(f"[HẾT GIỜ] chờ {what} quá {timeout:.0f} s")
        return False

    def run_sim(self, scenario: str, duration: float, seed: int = 42) -> None:
        if self.sim and self.sim.is_alive():
            self.sim.join()
        self.sim = threading.Thread(target=sim_cli.main, daemon=True,
                                    args=(["--scenario", scenario, "--duration", str(duration), "--seed", str(seed)],))
        self.sim.start()

    def n_batches(self) -> int:
        store = open_store(self.args.db)
        try:
            return len(store.batches(R, "ANCHORED"))
        finally:
            store.close()

    def zone(self) -> int | None:
        last = self.state().get("last")
        return last["zone"] if last else None

    # ---------------- kịch bản ----------------
    def run(self) -> int:
        try:
            with urllib.request.urlopen(f"{self.api}/api/health", timeout=3):
                pass
        except OSError:
            print(f"[LỖI] Không thấy backend ở {self.api} – chạy `hrc-backend` trước")
            return 2
        print(f"Contract {self.chain.address} trên chain {self.chain.chain_id}"
              + (f" – {self.explorer}/address/{self.chain.address}" if self.explorer else " (local)"))
        print("Dashboard: http://localhost:5173")
        if self.chain.robot_info(R).state == STATE_LOCKED:
            print("[CHÚ Ý] Robot đang LOCKED từ lần trước – hãy mở khóa trên dashboard trước khi demo")
            return 2
        try:
            op = ChainClient.from_env("OPERATOR")
            if self.chain.robot_info(R).current_operator.lower() != op.sender.lower():
                op.check_in(R)
        except ChainError:
            op = None  # không có khóa OPERATOR: bỏ qua phần check-in

        self.step(1, "Robot chạy bình thường")
        self.run_sim("normal", 12)
        self.wait(lambda: self.state()["device"]["online"] and self.zone() == 0, 20, "thiết bị online")
        self.sim.join()
        st = self.state()
        self.say(f"Zone SAFE, relay {'NGẮT' if st['last']['estop'] else 'ĐANG CHẠY'}, "
                 f"{self.n_batches()} batch log đã neo lên chuỗi")

        self.step(2, "Người đi vào vùng nguy hiểm")
        self.run_sim("approach", 18)
        self.wait(lambda: self.zone() == 2, 20, "DANGER")
        t_danger = time.monotonic()
        self.say(f"FAST LANE: zone DANGER -> relay NGẮT NGAY tại ESP32 (estop={self.state()['last']['estop']}), "
                 "không chờ mạng hay blockchain")
        self.wait(lambda: self.chain.robot_info(R).state == STATE_LOCKED, 120, "LOCKED on-chain")
        ev = [e for e in self.chain.get_events(self.chain.deploy_block(), self.chain.block_number(), ("EmergencyStop",))][-1]
        self.say(f"TRUST LANE: AI xác nhận người thật -> sự cố #{ev['args']['incidentId']} (mức {ev['args']['severity']}) "
                 f"đã nằm trên chuỗi {time.monotonic() - t_danger:.1f} s sau khi dashboard thấy DANGER")
        self.say(f"   {self.link(ev['tx_hash'])}")
        self.say(f"   bằng chứng (Merkle root 20 bản ghi): {'0x' + ev['args']['evidenceHash'].hex()[:20]}…")
        self.wait(lambda: self.state()["device"]["state"] == 1, 15, "thiết bị nhận LOCK")
        self.say("Backend nghe event EmergencyStop -> gửi LOCK (ký HMAC) -> thiết bị báo LOCKED")

        self.step(3, "Thử cho robot chạy lại khi đang khóa")
        if op:
            try:
                op.check_in(R)
                self.say("(không mong đợi) check-in thành công")
            except ChainError as e:
                self.say(f"Contract TỪ CHỐI: {e.error_name} – operator không nhận được robot đang LOCKED")
        self.say(f"Relay tại thiết bị vẫn NGẮT (estop={self.state()['last']['estop']}) dù người đã rời đi")
        self.sim.join()
        self.run_sim("normal", 600)

        self.step(4, "SUPERVISOR mở khóa")
        self.wait(lambda: self.zone() == 0, 30, "vùng làm việc SAFE")
        if self.args.auto_unlock:
            tx = ChainClient.from_env("SUPERVISOR").unlock(R)
            self.say(f"Script ký unlock() bằng ví SUPERVISOR: {self.link(tx.tx_hash)}")
        else:
            self.say(">>> Trên dashboard: bấm 'Mở khóa (ký bằng MetaMask)' rồi xác nhận trong MetaMask <<<")
        self.wait(lambda: self.chain.robot_info(R).state != STATE_LOCKED, 300, "unlock on-chain")
        self.wait(lambda: self.state()["device"]["state"] == 0, 30, "thiết bị nhận UNLOCK")
        self.say("Event Unlocked -> backend gửi UNLOCK (ký HMAC) -> thiết bị gỡ khóa, chạy lại sau 1 s SAFE")

        self.step(5, "Sửa 1 dòng log off-chain -> bị phát hiện")
        store = open_store(self.args.db)
        key = hmac_key()
        found = self.wait(lambda: any(any(t.zone == 2 for t in store.batch_records(R, b.batch_id))
                                      for b in store.batches(R, "ANCHORED")), 90, "batch chứa bản ghi DANGER được neo")
        batches = store.batches(R, "ANCHORED")
        b = next((x for x in reversed(batches) if any(t.zone == 2 for t in store.batch_records(R, x.batch_id))),
                 batches[-1]) if found else batches[-1]
        seq = pick_victim(store, R, b.batch_id)
        self.say(f"Batch #{b.batch_id}: đối soát trước khi sửa -> "
                 f"{'KHỚP' if verify_batch(store, self.chain, key, R, b.batch_id).ok else 'LỆCH'}")
        t = tamper(store, R, b.batch_id, seq)
        self.say(f"Sửa seq {seq}: DANGER -> SAFE, khoảng cách {t.old} -> {t.new} mm (che giấu sự cố)")
        v = verify_batch(store, self.chain, key, R, b.batch_id)
        self.say(f"Đối soát lại: Merkle root {'KHỚP' if v.root_match else 'LỆCH so với root trên chuỗi'}; "
                 f"HMAC chỉ ra dòng bị sửa: seq {v.hmac_bad_seqs}")
        restore(store, t)
        self.say("Đã khôi phục dòng bị sửa")
        store.close()
        print(f"\n{'=' * 78}\n HẾT DEMO – robot vẫn đang chạy kịch bản normal (Ctrl+C để dừng)\n{'=' * 78}", flush=True)
        try:
            self.sim.join()
        except KeyboardInterrupt:
            pass
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auto-unlock", action="store_true", help="script tự mở khóa bằng SUPERVISOR_PRIVATE_KEY")
    ap.add_argument("--pause", action="store_true", help="chờ Enter giữa các bước")
    ap.add_argument("--db", type=Path, help="DB của backend (nếu hrc-backend chạy với --db)")
    args = ap.parse_args()
    try:
        return Demo(args).run()
    except ChainError as e:
        print(f"[LỖI] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
