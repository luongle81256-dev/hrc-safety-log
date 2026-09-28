"""NGHIỆM THU PHASE 4 – chạy end-to-end IoT -> AI -> Smart Contract trên Hardhat node local.

Cần chạy sẵn:  Mosquitto (localhost:1883)  +  cd contracts; npx hardhat node
Chạy:          python scripts/e2e_local.py

Kịch bản:
  0. Deploy contract MỚI bằng Ignition (--reset), backend + simulator dùng DB/NVS tạm
  1. normal       : không có sự cố; log được neo theo batch
  2. approach     : người vào vùng DANGER -> AI xác nhận -> reportIncident -> LOCKED -> event -> LOCK xuống thiết bị
                    -> SUPERVISOR unlock -> event Unlocked -> UNLOCK xuống thiết bị; operator bị ghi 1 vi phạm
  3. overspeed    : robot vượt tốc -> sự cố KHÔNG quy lỗi operator -> LOCKED -> unlock
  4. sensor_noise : xung nhiễu làm zone DANGER nhưng AI KHÔNG tạo sự cố INTRUSION giả
  5. Mất kết nối  : chuỗi LOCKED khi thiết bị offline -> kết nối lại -> tự đồng bộ LOCK qua heartbeat
  6. Neo nốt log, đối soát MỌI batch với Merkle root on-chain (+ verifyRecord on-chain)
  7. Tamper: sửa 1 dòng log trong DB -> đối soát phát hiện batch bị sửa + chỉ ra đúng dòng (HMAC)
Thoát mã 0 nếu mọi kiểm tra ĐẠT.

LƯU Ý: KHÔNG chạy song song với pytest / hrc-sim / hrc-backend khác – tất cả dùng chung topic hrc/1/*
trên cùng broker nên sẽ giẫm lên nhau (simulator lạ phát telemetry, backend lạ gửi lệnh).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from hrc_ai.inference import HrcDetector  # noqa: E402
from hrc_backend.chain import STATE_ACTIVE, STATE_LOCKED, ZERO_ADDRESS, ChainClient, ChainError  # noqa: E402
from hrc_backend.ingest import now_ms  # noqa: E402
from hrc_backend.service import BackendService  # noqa: E402
from hrc_backend.settings import hmac_key  # noqa: E402
from hrc_backend.storage import LogStore  # noqa: E402
from hrc_backend.verify import verify_batch  # noqa: E402
from hrc_common.config import ZONE_DANGER, ZONE_SAFE  # noqa: E402
from hrc_sim import cli as sim_cli  # noqa: E402

R = 1  # robot
results: list[tuple[str, bool, str]] = []
log = logging.getLogger("e2e")


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"   {'✓ ĐẠT ' if ok else '✗ LỖI '} {name}{' – ' + detail if detail else ''}", flush=True)
    return ok


def wait_until(cond, timeout: float, step: float = 0.25) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def section(title: str) -> None:
    print(f"== {title}", flush=True)


def pick_noise_seed(ticks: int = 150) -> int:
    """Chọn seed sensor_noise chắc chắn có xung nhiễu gây DANGER trong `ticks` chu kỳ đầu.

    Thế giới mô phỏng chỉ phụ thuộc seed => quét offline cho kết quả giống hệt lúc chạy thật.
    """
    from hrc_sim.device import MemoryNvs, SimDevice
    from hrc_sim.offline import run_offline
    from hrc_sim.scenarios import make_scenario

    for seed in range(1, 200):
        steps = run_offline(make_scenario("sensor_noise", seed), SimDevice(R, bytes(range(32)), MemoryNvs()), ticks)
        if sum(s.sample.fault == "spike" and s.telemetry.zone == ZONE_DANGER for s in steps) >= 2:
            return seed
    raise RuntimeError("không tìm được seed phù hợp")


def deploy_fresh() -> None:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise SystemExit("Không tìm thấy npx (Node.js)")
    r = subprocess.run([npx, "hardhat", "ignition", "deploy", "ignition/modules/HRCSafetyLog.ts",
                        "--network", "localhost", "--reset"], cwd=ROOT / "contracts", capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"Deploy thất bại:\n{r.stdout}\n{r.stderr}")


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="      %(name)s: %(message)s")
    tmp = Path(tempfile.mkdtemp(prefix="hrc_e2e_"))
    section(f"0. Chuẩn bị (thư mục tạm {tmp})")
    try:
        deploy_fresh()
        device = ChainClient.from_env("DEVICE")
        supervisor = ChainClient.from_env("SUPERVISOR")
        operator = ChainClient.from_env("OPERATOR")
    except ChainError as e:
        print(f"[LỖI] {e}")
        return 2
    check("Deploy contract mới bằng Ignition", True, f"{device.address} trên chain {device.chain_id}")
    check("Vai trò đúng (DEVICE/SUPERVISOR/OPERATOR)",
          device.has_role("DEVICE_ROLE", device.sender) and supervisor.has_role("SUPERVISOR_ROLE", supervisor.sender)
          and operator.has_role("OPERATOR_ROLE", operator.sender))

    key = hmac_key()
    store = LogStore(tmp / "e2e.sqlite3")
    svc = BackendService(store, key, HrcDetector(), lambda: ChainClient.from_env("DEVICE"), batch_interval_ms=5000)
    svc.start()
    check("Backend sẵn sàng (MQTT + chain)", svc.ready.wait(20) and svc.mqtt_connected.wait(10))
    ticker_stop = threading.Event()

    def ticker():
        while not ticker_stop.wait(1.0):
            svc.tick()

    threading.Thread(target=ticker, daemon=True).start()
    operator.check_in(R)
    check("Operator check-in robot 1", device.robot_info(R).current_operator == operator.sender)

    def run_sim(scenario: str, duration: float, seed: int) -> threading.Thread:
        args = ["--scenario", scenario, "--duration", str(duration), "--seed", str(seed),
                "--state-dir", str(tmp / "nvs")]
        th = threading.Thread(target=sim_cli.main, args=(args,), daemon=True)
        th.start()
        return th

    def dev_state():
        st = store.device_statuses().get(R)
        return st.state if st and st.online else None

    def latest_zone():
        rows = store._db.execute("SELECT zone FROM telemetry WHERE robot_id=? ORDER BY seq DESC LIMIT 5", (R,)).fetchall()
        return [z for (z,) in rows]

    def incidents(kind=None):
        return [i for i in store.incidents(R) if kind is None or i.kind == kind]

    def unlock_and_wait(label: str) -> None:
        wait_until(lambda: latest_zone() and all(z == ZONE_SAFE for z in latest_zone()), 20)  # người đã rời đi
        supervisor.unlock(R)
        check(f"[{label}] SUPERVISOR unlock -> chuỗi ACTIVE", device.robot_info(R).state == STATE_ACTIVE)
        check(f"[{label}] Event Unlocked -> UNLOCK tới thiết bị (heartbeat state=ACTIVE)",
              wait_until(lambda: dev_state() == 0, 10))

    # ------------------------------------------------------------------ 1
    section("1. normal (10 s): vận hành bình thường")
    th = run_sim("normal", 10, 42)
    th.join()
    check("Thiết bị online, heartbeat có ký hợp lệ", dev_state() is not None)
    check("Không có sự cố", len(incidents()) == 0, f"{len(incidents())} sự cố")
    check("Log đã được neo theo batch", wait_until(lambda: len(store.batches(R, "ANCHORED")) >= 1, 10),
          f"{len(store.batches(R, 'ANCHORED'))} batch")

    # ------------------------------------------------------------------ 2
    section("2. approach: người vào vùng nguy hiểm")
    t_phase = now_ms()
    th = run_sim("approach", 20, 42)
    ok = check("AI xác nhận INTRUSION -> reportIncident thành công",
               wait_until(lambda: any(i.status == "SENT" for i in incidents("INTRUSION")), 25))
    if ok:
        inc = incidents("INTRUSION")[0]
        check("Sự cố quy lỗi đúng operator đang check-in", inc.operator.lower() == operator.sender.lower())
        check("Robot LOCKED trên chuỗi", device.robot_info(R).state == STATE_LOCKED)
        check("Event EmergencyStop -> LOCK tới thiết bị (heartbeat state=LOCKED)", wait_until(lambda: dev_state() == 1, 10))
        t_danger = store._db.execute("SELECT MIN(received_at_ms) FROM telemetry WHERE robot_id=? AND zone=? "
                                     "AND received_at_ms>=?", (R, ZONE_DANGER, t_phase)).fetchone()[0]
        t_lock = store._db.execute("SELECT MIN(sent_at_ms) FROM cmd_log WHERE robot_id=? AND cmd='LOCK' "
                                   "AND sent_at_ms>=?", (R, t_phase)).fetchone()[0]
        check("Độ trễ trust lane (DANGER tới backend -> gửi LOCK)", t_lock is not None and t_danger is not None,
              f"{t_lock - t_danger} ms (fast lane đã ngắt relay ngay tại ESP32 trước đó)")
        check("Operator bị ghi 1 vi phạm on-chain", device.violation_count(operator.sender) == 1)
        unlock_and_wait("approach")
    th.join()

    # ------------------------------------------------------------------ 3
    section("3. overspeed: robot vượt tốc")
    th = run_sim("overspeed", 12, 7)
    ok = check("AI phát hiện OVERSPEED -> reportIncident",
               wait_until(lambda: any(i.status == "SENT" for i in incidents("OVERSPEED")), 15))
    if ok:
        check("Sự cố máy KHÔNG quy lỗi operator", incidents("OVERSPEED")[0].operator == ZERO_ADDRESS)
        check("Vi phạm của operator không tăng", device.violation_count(operator.sender) == 1)
        check("LOCK tới thiết bị", wait_until(lambda: dev_state() == 1, 10))
        unlock_and_wait("overspeed")
    th.join()

    # ------------------------------------------------------------------ 4
    noise_seed = pick_noise_seed()
    section(f"4. sensor_noise (seed {noise_seed}): nhiễu cảm biến")
    n_intr = len(incidents("INTRUSION"))
    t_phase = now_ms()
    th = run_sim("sensor_noise", 15, noise_seed)
    th.join()
    time.sleep(1.5)
    spikes = store._db.execute("SELECT COUNT(*) FROM telemetry WHERE robot_id=? AND zone=? AND received_at_ms>=?",
                               (R, ZONE_DANGER, t_phase)).fetchone()[0]
    check("Có bản ghi DANGER do xung nhiễu (fast lane vẫn ngắt relay)", spikes > 0, f"{spikes} bản ghi")
    check("AI KHÔNG tạo sự cố INTRUSION giả", len(incidents("INTRUSION")) == n_intr)
    if device.robot_info(R).state == STATE_LOCKED:  # có thể có SENSOR_FAULT kéo dài => mở khóa trước bước 5
        supervisor.unlock(R)

    # ------------------------------------------------------------------ 5
    section("5. Mất kết nối: lệnh gửi lúc thiết bị offline bị mất -> tự đồng bộ khi kết nối lại")
    nvs_file = tmp / "nvs" / f"robot_{R}.json"

    def set_device_locked(locked: bool) -> None:
        """Đặt trạng thái khóa lưu trong NVS của thiết bị (trạng thái lúc nó mất kết nối)."""
        data = json.loads(nvs_file.read_text(encoding="utf-8"))
        data["locked"] = locked
        nvs_file.write_text(json.dumps(data), encoding="utf-8")

    # 5a. Chuỗi LOCKED trong khi thiết bị offline và đang tin mình ACTIVE
    svc.flush(60)  # không còn giao dịch nào của backend chạy song song (tránh trùng nonce với ví DEVICE)
    check("[5a] Thiết bị đã offline (hết hạn heartbeat)", wait_until(lambda: dev_state() is None, 6))
    device.report_incident(R, b"\x01" * 32, 1, ZERO_ADDRESS)
    set_device_locked(False)
    time.sleep(2)  # backend gửi LOCK nhưng không ai nhận
    th = run_sim("normal", 8, 42)
    check("[5a] Chuỗi LOCKED, thiết bị ACTIVE -> kết nối lại -> backend gửi lại LOCK",
          wait_until(lambda: dev_state() == 1, 7))
    unlock_and_wait("5a")
    th.join()
    # 5b. Chuỗi ACTIVE trong khi thiết bị offline và vẫn tin mình LOCKED (UNLOCK bị mất)
    check("[5b] Thiết bị đã offline (hết hạn heartbeat)", wait_until(lambda: dev_state() is None, 6))
    set_device_locked(True)
    th = run_sim("normal", 8, 42)
    check("[5b] Chuỗi ACTIVE, thiết bị LOCKED -> kết nối lại -> backend gửi lại UNLOCK",
          wait_until(lambda: dev_state() == 0, 7))
    th.join()

    # ------------------------------------------------------------------ 6
    section("6. Neo nốt log + đối soát với blockchain")
    check("Neo nốt log còn lại", svc.flush(60))
    unbatched = store._db.execute("SELECT COUNT(*) FROM telemetry WHERE batch_id IS NULL").fetchone()[0]
    total = store.count_telemetry(R)
    check("Mọi bản ghi đều đã thuộc 1 batch trên chuỗi", unbatched == 0, f"{total} bản ghi, còn {unbatched} chưa neo")
    batches = store.batches(R, "ANCHORED")
    vs = [verify_batch(store, device, key, R, b.batch_id) for b in batches]
    check("Merkle root Python == root on-chain cho MỌI batch", all(v.root_match for v in vs), f"{len(vs)} batch")
    n_chk = sum(v.onchain_checked for v in vs)
    check("verifyRecord on-chain (contract tự tính leaf) đúng", all(v.onchain_ok == v.onchain_checked for v in vs),
          f"{n_chk} bản ghi mẫu")

    # ------------------------------------------------------------------ 6
    section("7. Tamper: sửa 1 dòng log off-chain")
    b1 = batches[0]
    victim = store.batch_records(R, b1.batch_id)[len(store.batch_records(R, b1.batch_id)) // 2]
    store._db.execute("UPDATE telemetry SET distance_mm = distance_mm + 500 WHERE robot_id=? AND seq=?", (R, victim.seq))
    v = verify_batch(store, device, key, R, b1.batch_id)
    check("Đối soát phát hiện batch bị sửa (root lệch on-chain)", not v.root_match)
    check("HMAC thiết bị chỉ ra đúng dòng bị sửa", v.hmac_bad_seqs == [victim.seq], f"seq {v.hmac_bad_seqs}")

    # ------------------------------------------------------------------ tổng kết
    ticker_stop.set()
    svc.stop()
    gas = sum(b.gas_used or 0 for b in batches)
    sent = [i for i in store.incidents() if i.status == "SENT"]
    print("\n== TỔNG KẾT")
    print(f"   Bản ghi: {total} | batch neo: {len(batches)} (tổng gas {gas:,}) | sự cố on-chain: "
          + ", ".join(f"#{i.onchain_id} {i.kind} mức {i.severity}" for i in sent))
    print(f"   Bị loại ở ingest: {store.rejection_counts() or 'không'} | thống kê: {dict(svc.stats)}")
    store.close()
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n   KẾT QUẢ: {len(results) - len(failed)}/{len(results)} kiểm tra ĐẠT" + (f" – LỖI: {failed}" if failed else ""))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
