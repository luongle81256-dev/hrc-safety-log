"""Logic trust lane với FakeChain (mô phỏng luật contract): batcher, chính sách sự cố, listener, đối soát."""
from __future__ import annotations

from dataclasses import replace

import pytest

from backend_helpers import FakeClock, make_tel
from hrc_ai.inference import Prediction
from hrc_backend.batcher import MerkleBatcher
from hrc_backend.chain import ChainError, RobotInfo, TxResult
from hrc_backend.incidents import KIND_INTRUSION, KIND_OVERSPEED, KIND_SENSOR_FAULT, IncidentPolicy
from hrc_backend.listener import ChainEventListener
from hrc_backend.verify import verify_batch
from hrc_common.config import load_config
from hrc_common.merkle import leaf_hash, merkle_root, telemetry_leaf, verify_proof

CFG = load_config()
MAXR = CFG["merkle"]["batch_max_records"]
INTERVAL = CFG["merkle"]["batch_max_interval_ms"]


class FakeChain:
    """Làm đúng các luật anchorLog của HRCSafetyLog (batchId tuần tự, dải seq không chồng lấn)."""

    chain_id, address = 31337, "0xFAKE"

    def __init__(self):
        self.last_batch = 0
        self.last_to = 0
        self.batches: dict[int, tuple[bytes, int, int]] = {}
        self.fail_next = False
        self.head = 100
        self.events: list[dict] = []

    def robot_info(self, rid):
        return RobotInfo(True, "0xDEV", 0, self.last_batch, self.last_to, "0xOP")

    def anchor_log(self, rid, batch_id, root, from_seq, to_seq):
        if self.fail_next:
            self.fail_next = False
            raise ChainError("RPC lỗi giả lập")
        if batch_id != self.last_batch + 1:
            raise ChainError("InvalidBatchId", "InvalidBatchId")
        if self.last_batch and from_seq <= self.last_to:
            raise ChainError("SeqOverlap", "SeqOverlap")
        self.last_batch, self.last_to = batch_id, to_seq
        self.batches[batch_id] = (root, from_seq, to_seq)
        return TxResult(f"0xtx{batch_id}", 1, 84_540, None)

    def get_batch(self, rid, bid):
        root, f, t = self.batches.get(bid, (bytes(32), 0, 0))
        return root, f, t, 0

    def verify_record(self, bid, record, proof):
        root, f, t = self.batches[bid][0], *self.batches[bid][1:]
        return f <= record[1] <= t and verify_proof(leaf_hash(record), proof, root)

    def block_number(self):
        return self.head

    def get_events(self, a, b):
        return [e for e in self.events if a <= e["block"] <= b]


@pytest.fixture
def chain():
    return FakeChain()


def fill(store, key, clock, n, start_seq=1, age_ms=0):
    for i in range(n):
        t = make_tel(key, start_seq + i, clock.now + i)
        store.insert_telemetry(t, clock.now - age_ms)


# ---------------- batcher ----------------
def test_no_batch_before_due(store, key, clock, chain):
    fill(store, key, clock, 10)
    assert MerkleBatcher(store, clock).run_once(chain) == []


def test_batch_when_interval_elapsed(store, key, clock, chain):
    fill(store, key, clock, 10, age_ms=INTERVAL)
    [b] = MerkleBatcher(store, clock).run_once(chain)
    recs = store.batch_records(1, 1)
    assert (b.batch_id, b.from_seq, b.to_seq, b.n_records) == (1, 1, 10, 10)
    assert chain.batches[1][0] == merkle_root([telemetry_leaf(t) for t in recs])
    assert store.batches(1)[0].status == "ANCHORED"


def test_full_batches_split_by_max_records(store, key, clock, chain):
    fill(store, key, clock, 2 * MAXR + 5)
    done = MerkleBatcher(store, clock).run_once(chain)
    assert [b.n_records for b in done] == [MAXR, MAXR]  # 5 bản ghi còn lại chưa đến hạn
    assert chain.last_to == 2 * MAXR
    done = MerkleBatcher(store, clock).run_once(chain, force=True)
    assert [(b.batch_id, b.from_seq, b.to_seq) for b in done] == [(3, 2 * MAXR + 1, 2 * MAXR + 5)]


def test_anchor_failure_keeps_rows_for_retry(store, key, clock, chain):
    fill(store, key, clock, 5)
    chain.fail_next = True
    assert MerkleBatcher(store, clock).run_once(chain, force=True) == []
    assert store.batches() == [] and len(store.unbatched(1, 100)) == 5
    assert len(MerkleBatcher(store, clock).run_once(chain, force=True)) == 1


def test_recover_pending(store, key, clock, chain):
    fill(store, key, clock, 4)
    rows = [t for t, _ in store.unbatched(1, 10)]
    root = merkle_root([telemetry_leaf(t) for t in rows])
    # Sập SAU khi tx lên chuỗi nhưng TRƯỚC khi kịp xác nhận trong DB
    store.create_batch(1, 1, root.hex(), 1, 4, 4, clock.now)
    chain.anchor_log(1, 1, root, 1, 4)
    # Sập TRƯỚC khi tx lên chuỗi
    store.create_batch(1, 2, "ab" * 32, 5, 9, 5, clock.now)
    MerkleBatcher(store, clock).recover(chain)
    assert [(b.batch_id, b.status) for b in store.batches()] == [(1, "ANCHORED")]
    assert len(store.batch_records(1, 1)) == 4


def test_orphans_when_chain_ahead(store, key, clock, chain):
    """DB bị xóa nhưng chuỗi vẫn còn batch cũ: bản ghi seq cũ bị đánh dấu orphan, không làm kẹt batcher."""
    chain.last_batch, chain.last_to = 3, 50
    fill(store, key, clock, 60)  # seq 1..60, trong đó 1..50 trùng dải đã neo
    [b] = MerkleBatcher(store, clock).run_once(chain, force=True)
    assert (b.batch_id, b.from_seq, b.to_seq) == (4, 51, 60)


# ---------------- chính sách sự cố ----------------
def pred(label="NORMAL", zone=0, seq=1, conf=0.99):
    return Prediction(1, seq, zone, label, conf, {label: conf}, 0.3, False, 0.1, False)


def ev(key, clock, estop=1, n=20):
    return [make_tel(key, s, clock.now + s, zone=2, estop=estop) for s in range(1, n + 1)]


def test_intrusion_reported_immediately_then_muted(key, clock):
    pol = IncidentPolicy()
    e = ev(key, clock)
    req = pol.observe(pred("INTRUSION", 2), e)
    assert req and req.kind == KIND_INTRUSION and req.blame_operator
    assert req.severity == CFG["incident"]["severity_intrusion"]
    assert req.evidence_hash == merkle_root([telemetry_leaf(t) for t in e]) and (req.from_seq, req.to_seq) == (1, 20)
    assert pol.observe(pred("INTRUSION", 2), e) is None  # chờ Unlocked, không báo lặp
    pol.on_unlocked(1)
    assert pol.observe(pred("INTRUSION", 2), e) is not None


def test_intrusion_without_estop_is_most_severe(key, clock):
    req = IncidentPolicy().observe(pred("INTRUSION", 2), ev(key, clock, estop=0))
    assert req.severity == CFG["incident"]["severity_intrusion_no_estop"]


def test_intrusion_label_outside_danger_not_reported(key, clock):
    assert IncidentPolicy().observe(pred("INTRUSION", zone=1), ev(key, clock)) is None


@pytest.mark.parametrize("kind,need", [(KIND_OVERSPEED, CFG["incident"]["overspeed_min_consecutive"]),
                                       (KIND_SENSOR_FAULT, CFG["incident"]["sensor_fault_min_consecutive"])])
def test_machine_faults_need_consecutive_and_do_not_blame(key, clock, kind, need):
    pol, e = IncidentPolicy(), ev(key, clock)
    for _ in range(need - 1):
        assert pol.observe(pred(kind), e) is None
    pol.observe(pred("NORMAL"), e)  # chuỗi bị ngắt => đếm lại từ đầu
    for _ in range(need - 1):
        assert pol.observe(pred(kind), e) is None
    req = pol.observe(pred(kind), e)
    assert req and req.kind == kind and not req.blame_operator


def test_rearm_after_failed_report(key, clock):
    pol = IncidentPolicy()
    assert pol.observe(pred("INTRUSION", 2), ev(key, clock))
    pol.rearm(1)
    assert pol.observe(pred("INTRUSION", 2), ev(key, clock))


# ---------------- listener ----------------
def test_listener_skips_history_then_processes_in_order(store, chain):
    chain.events = [{"event": "EmergencyStop", "block": 50, "log_index": 0, "args": {}}]
    got = []
    lst = ChainEventListener(chain, store, got.append)
    assert lst.poll() == 0  # lần đầu: bắt đầu từ block hiện tại, KHÔNG phát lại lịch sử
    chain.events += [{"event": "EmergencyStop", "block": 101, "log_index": 0, "args": {}},
                     {"event": "Unlocked", "block": 102, "log_index": 0, "args": {}}]
    chain.head = 102
    assert lst.poll() == 2 and [e["event"] for e in got] == ["EmergencyStop", "Unlocked"]
    assert ChainEventListener(chain, store, got.append).poll() == 0  # khởi động lại: không xử lý lặp


# ---------------- đối soát ----------------
def test_verify_batch_detects_and_localizes_tamper(store, key, clock, chain):
    fill(store, key, clock, 30)
    MerkleBatcher(store, clock).run_once(chain, force=True)
    v = verify_batch(store, chain, key, 1, 1)
    assert v.ok and v.root_match and v.onchain_ok == v.onchain_checked == 3
    store._db.execute("UPDATE telemetry SET zone = 2 WHERE seq = 17")
    v = verify_batch(store, chain, key, 1, 1)
    assert not v.root_match and v.hmac_bad_seqs == [17] and not v.ok
