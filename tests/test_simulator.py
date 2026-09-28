"""Simulator: kịch bản có nhãn + fast lane (đặc tả hành vi cho firmware Phase 5) + bảo mật lệnh + NVS."""
from __future__ import annotations

from collections import Counter

import pytest

from backend_helpers import FakeClock
from hrc_backend.ingest import TelemetryIngestor
from hrc_common.config import ZONE_DANGER, ZONE_SAFE, load_config, ssm_params
from hrc_common.protocol import build_command
from hrc_common.ssm import classify_zone, protective_distance_mm
from hrc_sim.device import (
    CMD_BAD_FORMAT,
    CMD_BAD_HMAC,
    CMD_OK,
    CMD_REPLAY,
    CMD_STALE,
    FileNvs,
    MemoryNvs,
    SimDevice,
)
from hrc_sim.offline import VIRTUAL_EPOCH_MS, run_offline
from hrc_sim.scenarios import (
    LABEL_INTRUSION,
    LABEL_NORMAL,
    LABEL_OVERSPEED,
    LABEL_SENSOR_FAULT,
    SCENARIOS,
    make_scenario,
)

CFG = load_config()
P = ssm_params()
PERIOD = CFG["system"]["telemetry_period_ms"]
HOLD_TICKS = CFG["fastlane"]["estop_reset_hold_ms"] // PERIOD


def run(name, key, n=600, seed=1):
    return list(run_offline(make_scenario(name, seed), SimDevice(1, key), n))


# ---------------- kịch bản & nhãn ----------------
def test_scenarios_deterministic_with_seed():
    for name in SCENARIOS:
        a, b = make_scenario(name, 7), make_scenario(name, 7)
        assert [a.step() for _ in range(300)] == [b.step() for _ in range(300)], name


@pytest.mark.parametrize(
    "name,must_have",
    [("normal", {LABEL_NORMAL}), ("approach", {LABEL_NORMAL, LABEL_INTRUSION}),
     ("overspeed", {LABEL_NORMAL, LABEL_OVERSPEED}), ("sensor_noise", {LABEL_NORMAL, LABEL_SENSOR_FAULT}),
     ("mixed", {LABEL_NORMAL, LABEL_INTRUSION, LABEL_OVERSPEED, LABEL_SENSOR_FAULT})],
)
def test_scenario_labels(key, name, must_have):
    labels = Counter(s.sample.label for s in run(name, key, n=1200))
    assert must_have <= set(labels), labels
    if name == "normal":
        assert set(labels) == {LABEL_NORMAL}


def test_sensor_noise_has_all_fault_types(key):
    faults = {s.sample.fault for s in run("sensor_noise", key, n=3000)}
    assert {"spike", "dropout", "drift"} <= faults


# ---------------- fast lane ----------------
def test_boot_starts_with_relay_open(key):
    steps = run("normal", key, n=HOLD_TICKS + 5)
    assert steps[0].telemetry.estop == 1  # khởi động an toàn
    assert steps[-1].telemetry.estop == 0  # SAFE đủ lâu thì chạy


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_danger_always_trips_same_cycle(key, name):
    """Bất biến an toàn: chu kỳ nào zone = DANGER thì relay PHẢI ngắt ngay chu kỳ đó (độ trễ 0 chu kỳ)."""
    for s in run(name, key, n=1500):
        if s.telemetry.zone == ZONE_DANGER:
            assert s.telemetry.estop == 1


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_reported_zone_consistent_with_ssm(key, name):
    """Backend tính lại zone từ (distance, v) phải khớp zone thiết bị báo (dùng cho kiểm tra chéo ở AI)."""
    for s in run(name, key, n=800):
        t = s.telemetry
        assert classify_zone(t.distance_mm, t.v_robot_mmps) == t.zone


def test_real_intrusion_detected(key):
    """Người vào sâu trong S (ngay cả khi robot đứng yên) => DANGER + ngắt relay."""
    s0 = protective_distance_mm(0, P)
    deep = [s for s in run("approach", key, n=1200) if s.sample.true_distance_mm <= s0 - 10]
    assert deep, "kịch bản phải có lúc người vào sâu vùng nguy hiểm"
    assert all(s.telemetry.zone == ZONE_DANGER and s.telemetry.estop == 1 for s in deep)


def test_relay_recloses_only_after_safe_hold(key):
    """Relay chỉ đóng lại sau >= estop_reset_hold_ms SAFE liên tục."""
    steps = run("mixed", key, n=3000)
    reclosures = 0
    for i in range(1, len(steps)):
        if steps[i - 1].telemetry.estop == 1 and steps[i].telemetry.estop == 0:
            reclosures += 1
            window = steps[i - HOLD_TICKS: i + 1]
            assert all(s.telemetry.zone == ZONE_SAFE for s in window)
    assert reclosures >= 3


def test_overspeed_trips(key):
    steps = run("overspeed", key, n=1200)
    over = [s for s in steps if s.telemetry.v_robot_mmps > P.v_robot_limit_mmps]
    assert over, "phải có chu kỳ robot thực sự vượt tốc"
    assert all(s.telemetry.estop == 1 for s in over)
    # sau khi ngắt, robot không bao giờ chạy vượt tốc liên tiếp 2 chu kỳ
    for a, b in zip(steps, steps[1:]):
        assert not (a.telemetry.v_robot_mmps > P.v_robot_limit_mmps and b.telemetry.v_robot_mmps > P.v_robot_limit_mmps)


def test_warning_reduces_speed(key):
    dev = SimDevice(1, key)
    t0 = VIRTUAL_EPOCH_MS
    for i in range(HOLD_TICKS + 2):  # qua giai đoạn khởi động
        dev.tick(t0 + i * PERIOD, 1000, 200)
    s = protective_distance_mm(dev.v_applied, P)
    dev.tick(t0 + 100_000, s + 10, 200)  # vào WARNING
    assert dev.v_applied == 200 * CFG["fastlane"]["warning_speed_percent"] // 100


def test_mqtt_lost_forces_estop(key):
    dev = SimDevice(1, key)
    for i in range(HOLD_TICKS + 2):
        dev.tick(VIRTUAL_EPOCH_MS + i * PERIOD, 1000, 150)
    assert not dev.estop
    dev.force_estop("MQTT_LOST")
    assert dev.tick(VIRTUAL_EPOCH_MS + 10_000, 1000, 150).estop == 1


def test_all_sim_telemetry_accepted_by_backend(key, store):
    """Simulator và backend dùng chung giao thức: 100% bản tin hợp lệ được nhận."""
    clock = FakeClock(VIRTUAL_EPOCH_MS)
    ing = TelemetryIngestor(store, key, clock)
    steps = run("mixed", key, n=500)
    for s in steps:
        clock.now = s.telemetry.ts_ms
        assert ing.ingest("hrc/1/telemetry", s.telemetry.to_json().encode()).accepted
    assert store.count_telemetry(1) == 500


# ---------------- lệnh LOCK / UNLOCK ----------------
def _ready_device(key, nvs=None):
    dev = SimDevice(1, key, nvs)
    for i in range(HOLD_TICKS + 2):
        dev.tick(VIRTUAL_EPOCH_MS + i * PERIOD, 1000, 150)
    return dev


def test_lock_then_unlock_flow(key):
    dev = _ready_device(key)
    now = VIRTUAL_EPOCH_MS + 10_000
    assert dev.handle_command(build_command(key, 1, "LOCK", now).to_json(), now) == CMD_OK
    for i in range(30):  # dù SAFE bao lâu, LOCKED vẫn giữ relay ngắt
        assert dev.tick(now + i * PERIOD, 1000, 150).estop == 1
    now += 5_000
    assert dev.handle_command(build_command(key, 1, "UNLOCK", now).to_json(), now) == CMD_OK
    assert dev.tick(now, 1000, 150).estop == 1  # UNLOCK không bật relay ngay
    for i in range(1, HOLD_TICKS + 1):
        t = dev.tick(now + i * PERIOD, 1000, 150)
    assert t.estop == 0  # chỉ chạy lại sau khi SAFE đủ lâu


def test_unlock_does_not_override_danger(key):
    dev = _ready_device(key)
    now = VIRTUAL_EPOCH_MS + 10_000
    dev.handle_command(build_command(key, 1, "LOCK", now).to_json(), now)
    dev.handle_command(build_command(key, 1, "UNLOCK", now + 1).to_json(), now + 1)
    for i in range(50):
        assert dev.tick(now + i * PERIOD, 50, 150).estop == 1  # người vẫn trong vùng nguy hiểm


def test_command_security(key):
    dev = _ready_device(key)
    now = VIRTUAL_EPOCH_MS + 10_000
    lock = build_command(key, 1, "LOCK", now)
    assert dev.handle_command(lock.to_json(), now) == CMD_OK
    assert dev.handle_command(lock.to_json(), now) == CMD_REPLAY  # phát lại y hệt
    assert dev.handle_command(build_command(key, 1, "UNLOCK", now - 5).to_json(), now) == CMD_REPLAY  # nonce cũ
    stale = now - CFG["security"]["cmd_max_skew_ms"] - 1
    dev2 = _ready_device(key)
    assert dev2.handle_command(build_command(key, 1, "LOCK", stale).to_json(), now) == CMD_STALE
    assert dev.handle_command(build_command(key, 2, "UNLOCK", now + 1).to_json(), now) == CMD_BAD_HMAC  # ký cho robot 2
    assert dev.handle_command(build_command(bytes(range(1, 33)), 1, "UNLOCK", now + 2).to_json(), now) == CMD_BAD_HMAC
    tampered = build_command(key, 1, "LOCK", now + 3).to_json().replace("LOCK", "UNLOCK")
    assert dev.handle_command(tampered, now) == CMD_BAD_HMAC
    assert dev.handle_command(b"{}", now) == CMD_BAD_FORMAT
    assert dev.locked  # không lệnh giả nào gỡ được khóa


# ---------------- NVS: bền qua reset ----------------
def test_seq_never_repeats_across_reboots(tmp_path, key):
    path = tmp_path / "robot_1.json"
    block = CFG["system"]["seq_reserve_block"]
    seen: list[int] = []
    for boot in range(4):
        dev = SimDevice(1, key, FileNvs(path))
        n = 1500 if boot == 1 else 7  # 1 lần chạy vượt qua 1 khối đặt trước
        for i in range(n):
            seen.append(dev.tick(VIRTUAL_EPOCH_MS + (boot * 10_000 + i) * PERIOD, 1000, 150).seq)
    assert seen == sorted(seen) and len(set(seen)) == len(seen)  # tăng ngặt, không lặp
    jumps = [b - a for a, b in zip(seen, seen[1:]) if b - a > 1]
    assert len(jumps) == 3 and all(j <= block for j in jumps)  # chỉ nhảy khi reset, tối đa 1 khối


def test_lock_and_nonce_persist_across_reboot(tmp_path, key):
    path = tmp_path / "robot_1.json"
    dev = _ready_device(key, FileNvs(path))
    now = VIRTUAL_EPOCH_MS + 10_000
    lock = build_command(key, 1, "LOCK", now)
    assert dev.handle_command(lock.to_json(), now) == CMD_OK

    rebooted = SimDevice(1, key, FileNvs(path))  # mất điện rồi bật lại
    assert rebooted.locked  # reset KHÔNG gỡ được khóa
    for i in range(30):
        assert rebooted.tick(now + i * PERIOD, 1000, 150).estop == 1
    assert rebooted.handle_command(lock.to_json(), now) == CMD_REPLAY  # lệnh cũ vẫn bị chặn


def test_memory_nvs_isolated(key):
    a, b = SimDevice(1, key, MemoryNvs()), SimDevice(1, key, MemoryNvs())
    assert a.tick(VIRTUAL_EPOCH_MS, 1000, 0).seq == b.tick(VIRTUAL_EPOCH_MS, 1000, 0).seq == 1
