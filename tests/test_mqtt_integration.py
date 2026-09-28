"""Tích hợp THẬT qua broker: simulator -> Mosquitto -> backend ingest -> SQLite, kèm replay attack và lệnh LOCK.

Tự bỏ qua nếu không có broker ở MQTT_HOST:MQTT_PORT.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from conftest import TEST_KEY_HEX
from hrc_backend import ingest_service, send_cmd
from hrc_backend.storage import LogStore
from hrc_common.env import mqtt_settings
from hrc_sim import cli as sim_cli


def _broker_up() -> bool:
    s = mqtt_settings()
    try:
        with socket.create_connection((s.host, s.port), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [pytest.mark.mqtt, pytest.mark.skipif(not _broker_up(), reason="không có broker MQTT")]


def test_end_to_end_ingest_replay_and_lock(tmp_path, monkeypatch):
    db = tmp_path / "e2e.sqlite3"
    monkeypatch.setenv("HRC_HMAC_KEY_HEX", TEST_KEY_HEX)
    monkeypatch.setenv("SQLITE_PATH", str(db))

    ingest = threading.Thread(target=ingest_service.main, args=(["--db", str(db), "--duration", "7"],), daemon=True)
    ingest.start()
    time.sleep(1.0)  # chờ backend subscribe

    lock_at_ms: list[int] = []

    def send_lock():
        time.sleep(3.0)  # sau giai đoạn khởi động an toàn (relay đã đóng)
        lock_at_ms.append(time.time_ns() // 1_000_000)
        assert send_cmd.main(["LOCK"]) == 0

    locker = threading.Thread(target=send_lock, daemon=True)
    locker.start()
    # Kịch bản normal: người ở xa => estop chỉ có thể do khởi động hoặc do LOCK
    assert sim_cli.main(["--scenario", "normal", "--duration", "5", "--no-nvs",
                         "--attack", "replay", "--attack-every", "10"]) == 0
    locker.join()
    ingest.join(timeout=10)

    store = LogStore(db)
    try:
        rows = store.telemetry(1)
        assert len(rows) >= 40, f"quá ít bản ghi: {len(rows)}"
        assert store.rejection_counts().get("REPLAY", 0) >= 1  # tấn công replay bị chặn
        assert [r.seq for r in rows] == sorted({r.seq for r in rows})  # không trùng, tăng dần

        received = store._db.execute(
            "SELECT seq, estop, received_at_ms FROM telemetry WHERE robot_id=1 ORDER BY seq").fetchall()
        before = [e for _, e, t in received if 1500 < t - received[0][2] and t < lock_at_ms[0]]
        after = [e for _, e, t in received if t > lock_at_ms[0] + 500]
        assert before and all(e == 0 for e in before), "trước LOCK robot phải đang chạy"
        assert after and all(e == 1 for e in after), "sau LOCK relay phải ngắt"
        assert 1 in store.device_statuses()  # heartbeat có ký đã được nhận
    finally:
        store.close()
