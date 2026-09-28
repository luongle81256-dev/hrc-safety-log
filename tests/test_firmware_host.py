"""Kiểm chứng logic FIRMWARE C++ không cần phần cứng: biên dịch đúng các file src/ của firmware bằng g++,
rồi so TỪNG BƯỚC với simulator Python (đặc tả sống): fast lane, kiểm lệnh, JSON/HMAC, seq qua reset.

Tự bỏ qua nếu không có g++ hoặc chưa có ArduinoJson (chạy `pio run` trong iot_code/firmware 1 lần để tải).
"""
from __future__ import annotations

import random

import pytest

from hrc_common.config import load_config
from hrc_common.protocol import build_command, build_heartbeat, build_telemetry
from hrc_sim.device import CMD_OK, MemoryNvs, SimDevice
from hrc_sim.offline import VIRTUAL_EPOCH_MS
from hrc_sim.scenarios import SCENARIOS, make_scenario

KEY = bytes(range(32))
CFG = load_config()
PERIOD = CFG["system"]["telemetry_period_ms"]

pytestmark = [pytest.mark.gcc]


@pytest.fixture
def host(fw_host):
    return fw_host


def test_hmac_vectors_and_utils(host):
    """SHA-256/HMAC + chuỗi canonical + parse khóa + so sánh hằng thời gian."""
    assert host("VEC") == "OK"


# ---------------- fast lane: C++ == Python trên mọi kịch bản ----------------
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_fastlane_matches_simulator(host, scenario):
    rng = random.Random(scenario)
    dev = SimDevice(1, KEY, MemoryNvs())
    sc = make_scenario(scenario, 11)
    assert host("FLRESET 0") == "OK"
    lock_at = {rng.randrange(100, 700): "LOCK", rng.randrange(800, 1200): "UNLOCK"}  # xen lệnh khóa/mở
    mismatches = []
    for i in range(1500):
        now = VIRTUAL_EPOCH_MS + i * PERIOD
        if i in lock_at:
            assert dev.handle_command(build_command(KEY, 1, lock_at[i], now).to_json(), now) == CMD_OK
        s = sc.step()
        v_meas_py = dev.v_applied
        t = dev.tick(now, s.measured_distance_mm, s.v_cmd_mmps)
        got = host(f"FL {now} {s.measured_distance_mm} {s.v_cmd_mmps} {int(dev.locked)}")
        exp = f"{t.zone} {t.estop} {v_meas_py} {dev.v_applied}"
        if got != exp:
            mismatches.append((i, exp, got))
    assert mismatches == [], f"{len(mismatches)} chu kỳ lệch, ví dụ {mismatches[:3]}"


def test_fastlane_boundaries_match_simulator(host):
    """Giá trị BIÊN (simulator ngẫu nhiên hiếm khi chạm): tốc độ = giới hạn ±1, khoảng cách = S ±1, S+margin ±1."""
    from hrc_common.config import ssm_params
    from hrc_common.ssm import protective_distance_mm

    p = ssm_params()
    lim = p.v_robot_limit_mmps
    dev = SimDevice(1, KEY, MemoryNvs())
    assert host("FLRESET 0") == "OK"
    inputs = []
    for v in (lim - 1, lim, lim + 1, p.v_robot_nominal_mmps):
        inputs += [(2000, v)] * 15  # đủ lâu để relay đóng lại rồi chạy ở tốc độ v
        s = protective_distance_mm(v, p)
        for d in (s + p.warning_margin_mm + 1, s + p.warning_margin_mm, s + 1, s, s - 1, 2000):
            inputs += [(d, v)] * 3
    now = VIRTUAL_EPOCH_MS
    for i, (d, v) in enumerate(inputs):
        vm = dev.v_applied
        t = dev.tick(now + i * PERIOD, d, v)
        assert host(f"FL {now + i * PERIOD} {d} {v} 0") == f"{t.zone} {t.estop} {vm} {dev.v_applied}", f"bước {i}: d={d} v={v}"


# ---------------- kiểm lệnh: cùng kết quả chấp nhận/từ chối ----------------
def test_command_verification_matches_simulator(host):
    now = VIRTUAL_EPOCH_MS + 50_000
    skew = CFG["security"]["cmd_max_skew_ms"]
    good = build_command(KEY, 1, "LOCK", now)
    unlock = build_command(KEY, 1, "UNLOCK", now + 5)
    h64 = "a" * 64
    cases = [
        good.to_json(),                                                     # OK
        good.to_json(),                                                     # REPLAY
        build_command(KEY, 1, "UNLOCK", now - 10).to_json(),                # nonce cũ -> REPLAY
        build_command(KEY, 2, "UNLOCK", now + 1).to_json(),                 # ký cho robot 2 -> BAD_HMAC
        build_command(bytes(range(1, 33)), 1, "UNLOCK", now + 2).to_json(),  # sai khóa -> BAD_HMAC
        unlock.to_json().replace("UNLOCK", "LOCK"),                         # sửa cmd -> BAD_HMAC
        build_command(KEY, 1, "UNLOCK", now + skew + 1000).to_json(),       # lệch giờ -> STALE
        unlock.to_json(),                                                   # OK
        "{}", "không phải json", "[1,2]",
        '{"cmd":"LOCK","nonce":1}',                                         # thiếu hmac
        f'{{"cmd":"LOCK","nonce":1,"hmac":"{h64}","x":1}}',                 # thừa khóa
        f'{{"cmd":"lock","nonce":1,"hmac":"{h64}"}}',                       # chữ thường
        f'{{"cmd":"LOCK","nonce":1.5,"hmac":"{h64}"}}',                     # số thực
        f'{{"cmd":"LOCK","nonce":-1,"hmac":"{h64}"}}',                      # âm
        f'{{"cmd":"LOCK","nonce":true,"hmac":"{h64}"}}',                    # bool
        f'{{"cmd":"LOCK","nonce":9223372036854775808,"hmac":"{h64}"}}',     # > 2^63-1
        f'{{"cmd":"LOCK","nonce":1,"hmac":"{h64.upper()}"}}',               # hex hoa
        f'{{"cmd":"LOCK","nonce":1,"hmac":"{h64[:-1]}"}}',                  # thiếu 1 ký tự
    ]
    # Trùng khóa: 2 parser khác nhau có thể lấy giá trị khác nhau => cả 2 phía PHẢI từ chối
    dup = build_command(KEY, 1, "LOCK", now + 7)
    cases.append(f'{{"cmd":"LOCK","nonce":{dup.nonce},"hmac":"{dup.hmac}","cmd":"LOCK"}}')
    cases.append(f'{{"cmd":"UNLOCK","nonce":{dup.nonce},"hmac":"{dup.hmac}","cmd":"LOCK"}}')
    # Escape unicode: khóa "cmd" viết dạng cmd (lách đếm khóa) và giá trị LOCK viết dạng LOCK
    cases.append(f'{{"\\u0063md":"LOCK","nonce":{dup.nonce},"hmac":"{dup.hmac}","cmd":"LOCK"}}')
    cases.append(f'{{"cmd":"\\u004cOCK","nonce":{dup.nonce},"hmac":"{dup.hmac}"}}')
    cases.append(dup.to_json())  # bản gốc hợp lệ vẫn phải được chấp nhận sau các bản biến tấu
    dev = SimDevice(1, KEY, MemoryNvs())
    assert host("CMDRESET 0") == "OK"
    for payload in cases:
        exp = dev.handle_command(payload, now)
        got = host(f"CMD {payload.encode().hex()} {now} 1")
        assert got == exp, f"payload {payload!r}: Python {exp}, C++ {got}"


# ---------------- JSON + HMAC: firmware gửi ĐÚNG TỪNG BYTE như Python ----------------
def test_telemetry_and_heartbeat_json_byte_exact(host):
    rng = random.Random(3)
    for _ in range(300):
        f = dict(robot_id=rng.choice([1, 7, 2**32 - 1]), seq=rng.choice([0, 1, rng.randrange(2**63)]),
                 ts_ms=rng.randrange(2**63), distance_mm=rng.randrange(65536), v_robot_mmps=rng.randrange(65536),
                 zone=rng.randrange(3), estop=rng.randrange(2))
        py = build_telemetry(KEY, **f).to_json()
        assert host("TEL " + " ".join(str(v) for v in f.values())) == py
    for _ in range(50):
        ts, st, es, cfg = rng.randrange(2**63), rng.randrange(2), rng.randrange(2), "%016x" % rng.randrange(2**64)
        assert host(f"HB 1 {ts} {st} {es} {cfg}") == build_heartbeat(KEY, 1, ts, st, es, cfg).to_json()


# ---------------- cảm biến siêu âm HC-SR04 (nhánh hardware) ----------------
def test_ultrasonic_range_conversion(host):
    """Độ rộng xung ECHO -> mm theo QUY ƯỚC CHUNG (8190 = không thấy vật), làm tròn xuống (an toàn)."""
    from hrc_common.config import ssm_params

    vmax = ssm_params().distance_valid_max_mm
    assert host("US 0 0 0") == "8190 0"  # ECHO không lên: LỖI cảm biến => fast lane ngắt relay
    assert host("US 1 0 0") == "8190 1"  # không có tiếng vọng trong tầm: NGOÀI TẦM, cảm biến vẫn ổn
    assert host("US 1 1 5831") == "1000 1"  # 1 m: 5831 µs * 0,343 / 2 = 1000,02 mm
    for us in (0, 1, 175, 1166, 6997, 6998, 7000, 7003, 7004, 7005, 20000, 4_000_000_000):
        mm = us * 343 // 2000
        assert host(f"US 1 1 {us}") == f"{mm if mm <= vmax else 8190} 1", f"echo {us} µs"


def test_ultrasonic_object_at_protective_distance_reads_as_S(host):
    """Vật cách ĐÚNG S => ECHO ngắn nhất tương ứng phải đọc ra đúng S (không xa hơn) => fast lane vào DANGER."""
    from hrc_common.config import ssm_params
    from hrc_common.ssm import protective_distance_mm

    p = ssm_params()
    s = protective_distance_mm(p.v_robot_nominal_mmps, p)
    echo = -(-s * 2000 // 343)  # ceil: độ rộng ECHO nhỏ nhất ứng với quãng >= S
    assert host(f"US 1 1 {echo}") == f"{s} 1"


# ---------------- seq bền qua reset ----------------
def test_seq_allocator_matches_simulator(host):
    """Chip mới -> chạy 7 bản ghi -> reset -> 1500 (vượt 1 khối) -> reset -> 3: C++ và Python cho CÙNG dãy seq."""
    assert host("NVSRESET") == "OK"
    nvs = MemoryNvs()
    py_seqs, c_seqs = [], []
    for boot, n in enumerate((7, 1500, 3)):
        if boot:
            assert host("BOOT") == "OK"
        dev = SimDevice(1, KEY, nvs)
        for _ in range(n):
            py_seqs.append(dev._next_seq())
            c_seqs.append(int(host("SEQ")))
    assert c_seqs == py_seqs
    assert c_seqs == sorted(set(c_seqs))  # tăng ngặt, không lặp
