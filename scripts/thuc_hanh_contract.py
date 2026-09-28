"""THỰC HÀNH SMART CONTRACT – tự tay gọi từng hàm của HRCSafetyLog và xem contract phản ứng.

Dành cho người phụ trách contract: mỗi bước in ra "đang làm gì – vì sao – kết quả".
Dùng ROBOT 2 (script tự đăng ký) để không đụng dữ liệu robot 1 mà backend đang dùng.
Chạy lại nhiều lần được (batchId tự tăng theo trạng thái trên chuỗi).

Chuẩn bị (xem docs/NGUOI3_SMART_CONTRACT.md):
    cửa sổ 1:  cd contracts ; npx hardhat node
    cửa sổ 2:  cd contracts ; npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset
Chạy:
    python scripts/thuc_hanh_contract.py            # dừng chờ Enter sau mỗi bước
    python scripts/thuc_hanh_contract.py --no-pause
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from hrc_backend.chain import STATE_LOCKED, ChainClient, ChainError  # noqa: E402
from hrc_common.merkle import build_layers, get_proof, leaf_hash  # noqa: E402

R = 2  # robot dùng để thực hành
ZERO = "0x0000000000000000000000000000000000000000"
STATE = {0: "ACTIVE", 1: "LOCKED"}


class Lab:
    def __init__(self, pause: bool):
        self.pause = pause
        self.n = 0
        self.admin = ChainClient.from_env("ADMIN")
        self.dev = ChainClient.from_env("DEVICE")
        self.op = ChainClient.from_env("OPERATOR")
        self.sup = ChainClient.from_env("SUPERVISOR")
        self.aud = ChainClient.from_env("AUDITOR")

    def step(self, title: str, why: str) -> None:
        self.n += 1
        print(f"\n{'=' * 78}\n BƯỚC {self.n}. {title}\n{'=' * 78}")
        print(f"   Vì sao: {why}")

    def done(self) -> None:
        if self.pause:
            input("   (Enter để sang bước tiếp) ")

    def expect_revert(self, what: str, fn) -> None:
        try:
            fn()
            print(f"   [BẤT THƯỜNG] {what} lại thành công!")
        except ChainError as e:
            print(f"   => Contract TỪ CHỐI ({what}): {e.error_name}")
            print(f"      chi tiết: {e}")

    def state(self) -> str:
        return STATE[self.dev.robot_info(R).state]

    def run(self) -> None:
        print(f"Contract {self.dev.address} trên chain {self.dev.chain_id}")
        print(f"Ví: ADMIN {self.admin.sender[:10]}… DEVICE {self.dev.sender[:10]}… OPERATOR {self.op.sender[:10]}… "
              f"SUPERVISOR {self.sup.sender[:10]}… AUDITOR {self.aud.sender[:10]}…")

        # ------------------------------------------------------------------
        self.step("ADMIN đăng ký robot 2 và gắn nó với ví DEVICE",
                  "mỗi robot có 1 'danh tính' trên chuỗi; chỉ đúng ví DEVICE được gắn mới ghi log thay robot đó")
        try:
            tx = self.admin.send("registerRobot", R, self.dev.sender)
            print(f"   registerRobot({R}, DEVICE) -> tx {tx.tx_hash[:18]}…  gas {tx.gas_used}")
        except ChainError as e:
            print(f"   Robot {R} đã đăng ký từ lần chạy trước ({e.error_name}) – dùng tiếp")
        info = self.dev.robot_info(R)
        print(f"   getRobot({R}): device={info.device[:10]}…, state={STATE[info.state]}, lastBatchId={info.last_batch_id}")
        self.done()

        # ------------------------------------------------------------------
        self.step("Người KHÔNG có quyền thử làm việc của người khác",
                  "AccessControl: mỗi hàm chỉ 1 vai trò gọi được – phân quyền do CONTRACT kiểm, không do giao diện")
        self.expect_revert("OPERATOR gọi unlock", lambda: self.op.unlock(R))
        self.expect_revert("OPERATOR gọi anchorLog", lambda: self.op.anchor_log(R, 1, b"\x11" * 32, 1, 1))
        self.done()

        # ------------------------------------------------------------------
        self.step("DEVICE neo 1 batch log (Merkle root của 4 bản ghi) lên chuỗi",
                  "chuỗi chỉ giữ 32 byte 'dấu niêm phong'; log thật nằm ở SQLite – rẻ hơn ghi từng dòng ~256 lần")
        info = self.dev.robot_info(R)
        batch_id = info.last_batch_id + 1
        from_seq = info.last_to_seq + 1
        records = [(R, from_seq + i, 1_790_000_000_000 + 100 * i, 900 - 50 * i, 150, 0, 0) for i in range(4)]
        layers = build_layers([leaf_hash(r) for r in records])
        root = layers[-1][0]
        print(f"   4 bản ghi seq {from_seq}..{from_seq + 3} -> 4 leaf -> root 0x{root.hex()[:16]}…")
        tx = self.dev.anchor_log(R, batch_id, root, from_seq, from_seq + 3)
        print(f"   anchorLog(robot {R}, batch {batch_id}) -> gas {tx.gas_used}")
        on_root, f, t, at = self.dev.get_batch(R, batch_id)
        print(f"   getBatch: root 0x{bytes(on_root).hex()[:16]}… seq {f}..{t}  (khớp: {bytes(on_root) == root})")
        self.done()

        # ------------------------------------------------------------------
        self.step("Thử PHÁT LẠI batch cũ và ghi CHỒNG dải seq",
                  "batchId phải = batch trước + 1 và fromSeq > toSeq trước: không ai chèn/ghi đè lịch sử được")
        self.expect_revert("gửi lại đúng batch vừa rồi", lambda: self.dev.anchor_log(R, batch_id, root, from_seq, from_seq + 3))
        self.expect_revert("batch mới nhưng seq chồng lấn",
                           lambda: self.dev.anchor_log(R, batch_id + 1, b"\x33" * 32, from_seq + 2, from_seq + 9))
        self.done()

        # ------------------------------------------------------------------
        self.step("Kiểm 1 dòng log bằng verifyRecord (Merkle proof), rồi SỬA dòng đó",
                  "chứng minh 1 bản ghi thuộc batch chỉ cần vài hash 'hàng xóm'; sửa 1 số là proof sai ngay")
        rec = records[1]
        proof = get_proof(layers, 1)  # anh em của leaf 1 ở tầng 0 + anh em của cha nó ở tầng 1
        print(f"   bản ghi gốc   {rec} -> verifyRecord = {self.dev.verify_record(batch_id, rec, proof)}")
        fake = rec[:3] + (rec[3] + 500,) + rec[4:]
        print(f"   sửa distance  {fake} -> verifyRecord = {self.dev.verify_record(batch_id, fake, proof)}")
        print(f"   leafHash on-chain == Python: {self.dev.leaf_hash(rec) == leaf_hash(rec)}")
        self.done()

        # ------------------------------------------------------------------
        self.step("OPERATOR check-in nhận ca robot 2",
                  "gắn TRÁCH NHIỆM: sự cố xảy ra khi ai đang vận hành thì người đó bị ghi vi phạm")
        if self.dev.robot_info(R).current_operator.lower() != self.op.sender.lower():
            self.op.check_in(R)
        print(f"   currentOperator = {self.dev.robot_info(R).current_operator}")
        print(f"   violationCount(OPERATOR) trước sự cố = {self.dev.violation_count(self.op.sender)}")
        self.done()

        # ------------------------------------------------------------------
        self.step("DEVICE báo sự cố: người vào vùng nguy hiểm",
                  "reportIncident chuyển robot sang LOCKED, lưu bằng chứng và ghi vi phạm cho operator đang trực")
        _, incident_id = self.dev.report_incident(R, b"\x22" * 32, 2, ZERO)
        print(f"   reportIncident -> sự cố #{incident_id}, robot {R} = {self.state()}")
        print("   (ở đây truyền operator = 0 để bài thực hành không làm OPERATOR bị khóa quyền sau 3 lần chạy;"
              " backend thật truyền địa chỉ operator khi lỗi do người)")
        self.done()

        # ------------------------------------------------------------------
        self.step("Robot đang LOCKED: thử cho chạy lại",
                  "không ai 'lách' được: check-in bị từ chối, DEVICE cũng không tự mở khóa được")
        self.expect_revert("OPERATOR check-in lại", lambda: self.op.check_in(R))
        self.expect_revert("DEVICE tự unlock", lambda: self.dev.unlock(R))
        self.done()

        # ------------------------------------------------------------------
        self.step("SUPERVISOR mở khóa",
                  "chỉ 1 người thật chịu trách nhiệm mới mở được; event Unlocked ghi lại ví nào đã mở và mở lúc nào")
        tx = self.sup.unlock(R)
        info = self.dev.robot_info(R)
        print(f"   unlock -> gas {tx.gas_used}; robot {R} = {STATE[info.state]}, currentOperator = {info.current_operator}")
        print("   (unlock xóa operator hiện tại: ca mới phải check-in lại từ đầu)")
        self.expect_revert("unlock lần 2 khi đã ACTIVE", lambda: self.sup.unlock(R))
        self.done()

        # ------------------------------------------------------------------
        self.step("AUDITOR ghi kết quả đối soát batch lên chuỗi",
                  "kết luận của kiểm toán cũng thành bằng chứng không chối được (ai kiểm, lúc nào, khớp hay không)")
        tx = self.aud.record_audit(R, batch_id, True, b"\x44" * 32)
        print(f"   recordAudit(batch {batch_id}, passed=true) -> gas {tx.gas_used}")
        self.expect_revert("audit batch không tồn tại", lambda: self.aud.record_audit(R, 999_999, True, b"\x44" * 32))
        self.done()

        # ------------------------------------------------------------------
        self.step("Đọc lại toàn bộ lịch sử từ EVENT",
                  "event là 'nhật ký' rẻ và vĩnh viễn: backend và dashboard dựng lịch sử từ đây")
        head = self.dev.block_number()
        for ev in self.dev.get_events(max(0, head - 30), head):
            if ev["args"].get("robotId") == R or ev["event"] in ("AuditRecorded",):
                args = {k: (("0x" + v.hex()[:12] + "…") if isinstance(v, bytes) else v) for k, v in ev["args"].items()}
                print(f"   block {ev['block']:>4}  {ev['event']:<19} {args}")
        print("\nXONG. Mở docs/NGUOI3_SMART_CONTRACT.md để đối chiếu từng bước với code contract.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-pause", action="store_true", help="chạy liền, không chờ Enter")
    args = ap.parse_args()
    try:
        Lab(pause=not args.no_pause).run()
    except ChainError as e:
        print(f"[LỖI] {e}\n=> Hardhat node đã chạy và đã deploy chưa? (xem phần chuẩn bị ở đầu file)")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
