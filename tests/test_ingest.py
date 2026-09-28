"""Backend ingest: chấp nhận / loại (HMAC, replay, parse...), khoảng trống seq, heartbeat."""
from __future__ import annotations

from dataclasses import replace

import pytest

from backend_helpers import make_tel
from hrc_backend.ingest import HeartbeatMonitor, TelemetryIngestor
from hrc_backend.storage import LogStore
from hrc_common.config import config_hash, load_config
from hrc_common.protocol import build_heartbeat, lwt_payload

TOPIC = "hrc/1/telemetry"
HB_TOPIC = "hrc/1/heartbeat"


@pytest.fixture
def ing(store, key, clock):
    return TelemetryIngestor(store, key, clock)


def _send(ing, t, topic=TOPIC):
    return ing.ingest(topic, t.to_json().encode())


# ---------------- chấp nhận ----------------
def test_valid_telemetry_stored(ing, store, key, clock):
    t = make_tel(key, 1, clock.now, distance_mm=333, zone=1)
    r = _send(ing, t)
    assert r.accepted and r.reason is None
    assert store.telemetry(1) == [t]
    assert store.last_seq(1) == 1


# ---------------- HMAC ----------------
def test_forged_payload_rejected_and_logged(ing, store, key, clock):
    t = make_tel(key, 1, clock.now)
    forged = replace(t, distance_mm=t.distance_mm + 500)  # sửa dữ liệu, giữ nguyên chữ ký
    r = _send(ing, forged)
    assert not r.accepted and r.reason == "HMAC"
    assert store.count_telemetry() == 0
    assert store.rejection_counts() == {"HMAC": 1}


def test_wrong_key_rejected(ing, clock):
    t = make_tel(bytes(range(1, 33)), 1, clock.now)
    assert _send(ing, t).reason == "HMAC"


def test_forged_message_does_not_advance_seq(ing, store, key, clock):
    """Bản tin giả với seq rất lớn KHÔNG được làm hỏng bộ đếm chống replay."""
    forged = replace(make_tel(key, 10**9, clock.now), zone=0)
    forged = replace(forged, hmac="0" * 64)
    assert _send(ing, forged).reason == "HMAC"
    assert store.last_seq(1) is None
    assert _send(ing, make_tel(key, 1, clock.now)).accepted


# ---------------- replay ----------------
def test_exact_replay_rejected(ing, store, key, clock):
    t = make_tel(key, 1, clock.now)
    assert _send(ing, t).accepted
    r = _send(ing, t)
    assert not r.accepted and r.reason == "REPLAY"
    assert "seq cuối" in r.detail  # bị chặn ở lớp 1 (bộ đếm), không phải nhờ khóa chính DB (lớp 2)
    assert store.count_telemetry() == 1


def test_db_primary_key_is_second_layer(store, key, clock):
    """Lớp 2: kể cả khi bộ đếm trong RAM bị bỏ qua, khóa chính (robot_id, seq) vẫn chặn trùng."""
    t = make_tel(key, 1, clock.now)
    assert store.insert_telemetry(t, clock.now)
    assert not store.insert_telemetry(replace(t, distance_mm=1), clock.now)


def test_old_seq_rejected(ing, key, clock):
    for s in (1, 2, 3):
        assert _send(ing, make_tel(key, s, clock.now + s)).accepted
    r = _send(ing, make_tel(key, 2, clock.now + 10))  # bản ghi ký hợp lệ nhưng seq cũ
    assert r.reason == "REPLAY"


def test_replay_protection_survives_backend_restart(tmp_path, key, clock):
    db = tmp_path / "persist.sqlite3"
    s1 = LogStore(db)
    ing1 = TelemetryIngestor(s1, key, clock)
    t = make_tel(key, 5, clock.now)
    assert _send(ing1, t).accepted
    s1.close()

    s2 = LogStore(db)  # "khởi động lại" backend
    ing2 = TelemetryIngestor(s2, key, clock)
    assert _send(ing2, t).reason == "REPLAY"
    assert _send(ing2, make_tel(key, 4, clock.now)).reason == "REPLAY"
    assert _send(ing2, make_tel(key, 6, clock.now)).accepted
    s2.close()


def test_seq_counters_independent_per_robot(store, key, clock, monkeypatch):
    cfg = load_config()
    monkeypatch.setitem(cfg["system"], "robot_ids", [1, 2])
    ing = TelemetryIngestor(store, key, clock)
    assert _send(ing, make_tel(key, 7, clock.now, robot_id=1)).accepted
    assert _send(ing, make_tel(key, 7, clock.now, robot_id=2), "hrc/2/telemetry").accepted


# ---------------- khoảng trống seq (mất gói QoS 0) ----------------
def test_gap_detected(ing, store, key, clock):
    for s in (1, 2, 6, 7, 10):
        r = _send(ing, make_tel(key, s, clock.now + s))
        assert r.accepted
    assert store.gaps(1) == [(3, 5), (8, 9)]


# ---------------- robot / topic ----------------
def test_unknown_robot_rejected(ing, key, clock):
    r = _send(ing, make_tel(key, 1, clock.now, robot_id=99), "hrc/99/telemetry")
    assert r.reason == "ROBOT_UNKNOWN"


def test_payload_robot_mismatch_topic(ing, store, key, clock, monkeypatch):
    monkeypatch.setitem(load_config()["system"], "robot_ids", [1, 2])
    ing = TelemetryIngestor(store, key, clock)
    r = _send(ing, make_tel(key, 1, clock.now, robot_id=2), TOPIC)  # robot 2 gửi vào topic robot 1
    assert r.reason == "ROBOT_MISMATCH"


@pytest.mark.parametrize("topic,reason", [("hrc/1/cmd", "KIND"), ("hrc/01/telemetry", "TOPIC"), ("x/1/telemetry", "TOPIC")])
def test_bad_topic(ing, key, clock, topic, reason):
    assert _send(ing, make_tel(key, 1, clock.now), topic).reason == reason


# ---------------- parse ----------------
@pytest.mark.parametrize(
    "payload,reason",
    [(b"not json", "JSON"), (b'{"robot_id":1}', "SCHEMA"), (b"x" * 2000, "SIZE"),
     (b'{"robot_id":1,"seq":1,"seq":2}', "JSON")],
)
def test_malformed_payload_rejected(ing, store, payload, reason):
    r = ing.ingest(TOPIC, payload)
    assert r.reason == reason
    assert store.rejection_counts() == {reason: 1}


def test_rejected_payload_truncated(tmp_path, key, clock):
    s = LogStore(tmp_path / "t.sqlite3", rejected_payload_max_bytes=16)
    TelemetryIngestor(s, key, clock).ingest(TOPIC, b"y" * 400)
    stored = s._db.execute("SELECT payload FROM rejected").fetchone()[0]
    assert len(stored) == 16
    s.close()


# ---------------- thời gian ----------------
def test_future_timestamp_rejected(ing, key, clock):
    skew = load_config()["security"]["ts_max_future_skew_ms"]
    assert _send(ing, make_tel(key, 1, clock.now + skew + 1)).reason == "FUTURE_TS"
    assert _send(ing, make_tel(key, 2, clock.now + skew)).accepted


# ---------------- heartbeat ----------------
@pytest.fixture
def mon(store, key, clock):
    return HeartbeatMonitor(store, key, clock)


def _hb(key, ts, cfg=None, state=0):
    return build_heartbeat(key, 1, ts, state, 0, cfg or config_hash()).to_json().encode()


def test_heartbeat_online_then_timeout(mon, key, clock):
    r = mon.ingest(HB_TOPIC, _hb(key, clock.now))
    assert r.accepted and r.event == "ONLINE"
    assert mon.status[1].online and mon.status[1].cfg_match
    clock.advance(load_config()["system"]["failsafe_timeout_ms"] + 1)
    assert mon.check_timeouts() == [1]
    assert not mon.status[1].online and mon.status[1].offline_reason == "TIMEOUT"


def test_heartbeat_lwt_marks_offline(mon, key, clock):
    mon.ingest(HB_TOPIC, _hb(key, clock.now))
    r = mon.ingest(HB_TOPIC, lwt_payload(1).encode())
    assert r.accepted and r.event == "OFFLINE"
    assert mon.status[1].offline_reason == "LWT"


def test_heartbeat_replay_and_forgery_rejected(mon, key, clock):
    hb = _hb(key, clock.now)
    assert mon.ingest(HB_TOPIC, hb).accepted
    assert mon.ingest(HB_TOPIC, hb).reason == "REPLAY"
    forged = _hb(bytes(range(1, 33)), clock.now + 100)
    assert mon.ingest(HB_TOPIC, forged).reason == "HMAC"


def test_heartbeat_cfg_mismatch_flagged(mon, key, clock):
    r = mon.ingest(HB_TOPIC, _hb(key, clock.now, cfg="0123456789abcdef"))
    assert r.accepted and r.event == "CFG_MISMATCH"
    assert mon.status[1].cfg_match is False


# ---------------- nonce lệnh ----------------
def test_cmd_nonce_strictly_increasing_even_if_clock_goes_back(tmp_path):
    db = tmp_path / "cmd.sqlite3"
    s = LogStore(db)
    n1 = s.next_cmd_nonce(1, 1_000_000, "LOCK")
    n2 = s.next_cmd_nonce(1, 1_000_000, "UNLOCK")  # cùng mili-giây
    n3 = s.next_cmd_nonce(1, 500_000, "LOCK")  # đồng hồ lùi
    s.close()
    s = LogStore(db)  # khởi động lại
    n4 = s.next_cmd_nonce(1, 1, "LOCK")
    s.close()
    assert n1 < n2 < n3 < n4
