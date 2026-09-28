"""Phase 8: hồ sơ mạng (.env.<mạng>), DB gắn với contract, nghiệp vụ auditor/tamper, chạy thử deploy Sepolia."""
from __future__ import annotations

import os
import socket
import subprocess
import sys

import pytest

from backend_helpers import make_tel
from hrc_backend.audit import pick_victim, record_audit, report_hash, restore, tamper, verify_all
from hrc_backend.batcher import MerkleBatcher
from hrc_backend.service import open_store_for_contract
from hrc_common import env as env_mod
from hrc_common.config import ROOT_DIR
from test_backend_chain import FakeChain


# ---------------- hồ sơ mạng ----------------
def test_network_profile_precedence(tmp_path, monkeypatch):
    """Thứ tự ưu tiên: biến môi trường thật > .env.<mạng> > .env"""
    (tmp_path / ".env").write_text("A=from_env\nB=from_env\nC=from_env\n", encoding="utf-8")
    (tmp_path / ".env.testnet").write_text("A=from_profile\nB=from_profile\nD=${A}-x\n", encoding="utf-8")
    monkeypatch.setattr(env_mod, "ROOT_DIR", tmp_path)
    for k in "ABCD":
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("B", "from_process")
    monkeypatch.setenv("HRC_NETWORK", "testnet")
    env_mod.load_env()
    assert (os.environ["A"], os.environ["B"], os.environ["C"], os.environ["D"]) == \
        ("from_profile", "from_process", "from_env", "from_profile-x")


def test_missing_profile_is_explicit_error(tmp_path, monkeypatch):
    monkeypatch.setattr(env_mod, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("HRC_NETWORK", "sepolia")
    with pytest.raises(FileNotFoundError, match=".env.sepolia"):
        env_mod.load_env()


def test_sepolia_example_is_committed_but_real_file_ignored():
    gi = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.*" in gi and "!.env.sepolia.example" in gi
    assert (ROOT_DIR / ".env.sepolia.example").exists()


# ---------------- DB gắn với đúng 1 contract ----------------
def test_store_archived_when_contract_changes(tmp_path, key, clock):
    db = tmp_path / "log.sqlite3"
    s = open_store_for_contract(db, 31337, "0xAAA")
    s.insert_telemetry(make_tel(key, 1, clock.now), clock.now)
    s.close()
    s = open_store_for_contract(db, 31337, "0xaaa")  # cùng contract (khác hoa/thường) => giữ nguyên
    assert s.count_telemetry() == 1
    s.close()
    s = open_store_for_contract(db, 31337, "0xBBB")  # deploy lại => DB mới, DB cũ được cất (không xóa)
    assert s.count_telemetry() == 0
    s.close()
    archived = [p for p in tmp_path.iterdir() if p.name.startswith("log.") and p.name != "log.sqlite3"
                and p.suffix == ".sqlite3"]
    assert len(archived) == 1


def test_store_archived_when_hardhat_restarted_same_address(tmp_path, key, clock):
    """Hardhat node khởi động lại + deploy lại => CÙNG địa chỉ 0x5FbDB… nhưng block deploy khác
    => phải cất DB cũ (lỗi thật đã gặp: batch_id trùng, UNIQUE constraint failed)."""
    db = tmp_path / "hw.sqlite3"
    addr = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    s = open_store_for_contract(db, 31337, addr, "0xaaa1")
    s.insert_telemetry(make_tel(key, 1, clock.now), clock.now)
    s.close()
    s = open_store_for_contract(db, 31337, addr, "0xAAA1")  # cùng chuỗi, chạy lại backend => giữ DB
    assert s.count_telemetry() == 1
    s.close()
    s = open_store_for_contract(db, 31337, addr, "0xbbb2")  # chuỗi mới, cùng địa chỉ => DB mới
    assert s.count_telemetry() == 0
    s.close()


# ---------------- auditor / tamper ----------------
@pytest.fixture
def anchored(store, key, clock):
    chain = FakeChain()
    for s in range(1, 41):
        store.insert_telemetry(make_tel(key, s, clock.now + s, zone=2 if s == 25 else 0), clock.now)
    b = MerkleBatcher(store, clock, max_records=20)
    b.run_once(chain)  # 2 batch đầy
    return chain


def test_verify_all_and_tamper_restore(store, key, anchored):
    assert [v.ok for v in verify_all(store, anchored, key, 1)] == [True, True]
    seq = pick_victim(store, 1, 2)
    assert seq == 25  # ưu tiên bản ghi DANGER (kịch bản che giấu sự cố)
    t = tamper(store, 1, 2, seq)
    res = verify_all(store, anchored, key, 1)
    assert res[0].ok and not res[1].ok and res[1].hmac_bad_seqs == [25]
    restore(store, t)
    assert all(v.ok for v in verify_all(store, anchored, key, 1))


def test_report_hash_changes_with_result(store, key, anchored):
    v_ok = verify_all(store, anchored, key, 1)[1]
    t = tamper(store, 1, 2, 25)
    v_bad = verify_all(store, anchored, key, 1)[1]
    restore(store, t)
    assert report_hash(v_ok) != report_hash(v_bad) and len(report_hash(v_ok)) == 32


def test_record_audit_sends_result(store, key, anchored):
    calls = []

    class Auditor:
        def record_audit(self, robot_id, batch_id, passed, rh):
            calls.append((robot_id, batch_id, passed, rh))
            return "tx"

    v = verify_all(store, anchored, key, 1)[0]
    record_audit(Auditor(), v)
    assert calls == [(1, 1, True, report_hash(v))]


# ---------------- chạy thử deploy Sepolia trên node local ----------------
def _node_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8545), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.chain
@pytest.mark.skipif(not _node_up(), reason="không có Hardhat node")
def test_deploy_sepolia_dry_run_local():
    """Đúng đường deploy Sepolia (mạng `sepolia`, 1 ví, file tham số vai trò) chạy trên node local."""
    r = subprocess.run([sys.executable, str(ROOT_DIR / "scripts" / "deploy_sepolia.py"), "--dry-run-local"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "vai trò + robot 1 đúng trên chuỗi" in r.stdout
