"""API dashboard (FastAPI): REST + WebSocket nhận sự kiện phát từ luồng nền + đối soát batch."""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from backend_helpers import make_tel
from hrc_backend.api import create_app
from hrc_backend.batcher import MerkleBatcher
from hrc_backend.hub import EventHub
from test_backend_chain import FakeChain

STATE = {"robots": [{"robot_id": 1, "chain_state": 0}], "stats": {}}


@pytest.fixture
def setup(store, key, clock):
    hub, chain = EventHub(), FakeChain()
    app = create_app(store, hub, key, lambda: chain, lambda: STATE, {"chain_id": 31337, "address": "0xABC"})
    with TestClient(app) as client:  # `with` => chạy lifespan (gắn event loop cho hub)
        yield client, hub, chain


def test_health_and_config(setup):
    client, *_ = setup
    assert client.get("/api/health").json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert cfg["chain_id"] == 31337 and cfg["contract_address"] == "0xABC"
    assert cfg["explorer"] is None  # local không có Etherscan
    names = {i.get("name") for i in cfg["abi"]}
    assert {"unlock", "EmergencyStop", "Unlocked", "hasRole"} <= names
    assert cfg["zones"] == {"SAFE": 0, "WARNING": 1, "DANGER": 2}


def test_state(setup):
    client, *_ = setup
    assert client.get("/api/state").json() == STATE


def test_websocket_snapshot_then_events_from_other_thread(setup):
    client, hub, _ = setup
    with client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["state"] == STATE
        # Sự kiện phát từ luồng KHÁC (giống luồng MQTT / chain) phải tới được client
        t = threading.Thread(target=lambda: [hub.publish({"type": "telemetry", "robot_id": 1, "seq": i})
                                             for i in range(3)])
        t.start()
        t.join()
        assert [ws.receive_json()["seq"] for _ in range(3)] == [0, 1, 2]
    assert [m["seq"] for m in client.get("/api/telemetry/1").json()] == [0, 1, 2]  # có lịch sử ngắn


def test_slow_client_does_not_block_publisher(setup):
    _, hub, _ = setup
    q = hub.subscribe()
    for i in range(1500):  # vượt QUEUE_MAX
        hub._fanout({"type": "x", "i": i})
    assert q.qsize() == 1000 and hub.dropped == 500
    assert q.get_nowait()["i"] == 500  # bản tin cũ nhất bị bỏ trước


def test_verify_endpoint_ok_then_tampered(setup, store, key, clock):
    client, _, chain = setup
    for s in range(1, 21):
        store.insert_telemetry(make_tel(key, s, clock.now + s), clock.now)
    MerkleBatcher(store, clock).run_once(chain, force=True)
    r = client.post("/api/verify/1/1").json()
    assert r["ok"] and r["root_match"] and r["n_records"] == 20
    assert client.get("/api/batches").json()[0]["batch_id"] == 1

    store._db.execute("UPDATE telemetry SET distance_mm = 1 WHERE seq = 7")
    r = client.post("/api/verify/1/1").json()
    assert not r["ok"] and not r["root_match"] and r["hmac_bad_seqs"] == [7]
    assert client.post("/api/verify/1/99").status_code == 404
