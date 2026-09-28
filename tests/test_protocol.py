"""Giao thức: chuỗi canonical, HMAC (test vector cho firmware), parse chặt."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from hrc_common.protocol import (
    INT63_MAX,
    Command,
    Heartbeat,
    LastWill,
    ProtocolError,
    build_command,
    build_heartbeat,
    build_telemetry,
    canonical_command,
    canonical_heartbeat,
    canonical_telemetry,
    load_key,
    lwt_payload,
    parse_command,
    parse_heartbeat,
    parse_telemetry,
    parse_topic,
    sign,
    verify,
    verify_command,
    verify_heartbeat,
    verify_telemetry,
)

# =============================================================================
# TEST VECTOR – firmware C (mbedtls) PHẢI ra đúng các giá trị này.
# Khóa = 00 01 02 ... 1f. Đã đối chiếu độc lập bằng .NET HMACSHA256.
# =============================================================================
VECTORS = [
    ("1|42|1790000000000|350|150|1|0", "0544056a530339a4f2a92ea818bb779c49c2e506d580c1f68f29cc13ad985d07"),
    ("1|LOCK|1790000000123", "430facbad90a2666d5846882c8bca4e719dcf26c49a0966fd458f7a84a78fad4"),
    ("hb|1|1790000000500|0|0|99c3bd15cba71f37", "2f836d5d8aafc121930eff7f34322a583b56ca03e3454303c47a41491a34d8f8"),
]

TEL = dict(robot_id=1, seq=42, ts_ms=1790000000000, distance_mm=350, v_robot_mmps=150, zone=1, estop=0)


@pytest.mark.parametrize("msg,expected", VECTORS)
def test_hmac_test_vectors(key, msg, expected):
    assert sign(key, msg) == expected
    assert verify(key, msg, expected)


def test_canonical_strings_exact():
    assert canonical_telemetry(**TEL) == "1|42|1790000000000|350|150|1|0"
    assert canonical_command(1, "LOCK", 1790000000123) == "1|LOCK|1790000000123"
    assert canonical_heartbeat(1, 1790000000500, 0, 0, "99c3bd15cba71f37") == "hb|1|1790000000500|0|0|99c3bd15cba71f37"
    assert canonical_telemetry(**{**TEL, "seq": 0, "zone": 0}) == "1|0|1790000000000|350|150|0|0"  # số 0 viết là "0"


# ---------------- telemetry ----------------
def test_telemetry_roundtrip(key):
    t = build_telemetry(key, **TEL)
    assert t.hmac == VECTORS[0][1]
    parsed = parse_telemetry(t.to_json())
    assert parsed == t
    assert verify_telemetry(key, parsed)


def test_field_order_in_json_does_not_matter(key):
    t = build_telemetry(key, **TEL)
    reordered = json.dumps(dict(reversed(list(json.loads(t.to_json()).items()))))
    assert verify_telemetry(key, parse_telemetry(reordered))


@pytest.mark.parametrize("field", list(TEL))
def test_any_field_change_breaks_hmac(key, field):
    t = build_telemetry(key, **TEL)
    new_value = 0 if field in ("zone", "estop") and TEL[field] else getattr(t, field) + 1
    assert not verify_telemetry(key, replace(t, **{field: new_value}))


def test_wrong_key_rejected(key):
    t = build_telemetry(key, **TEL)
    assert not verify_telemetry(bytes(range(1, 33)), t)


def test_uppercase_hmac_rejected(key):
    t = build_telemetry(key, **TEL)
    with pytest.raises(ProtocolError) as e:
        parse_telemetry(t.to_json().replace(t.hmac, t.hmac.upper()))
    assert e.value.reason == "SCHEMA"


def _payload(**over) -> str:
    obj = {**TEL, "hmac": "a" * 64, **over}
    return json.dumps({k: v for k, v in obj.items() if v is not ...})


@pytest.mark.parametrize(
    "payload,reason",
    [
        ("không phải json", "JSON"),
        (b"\xff\xfe", "JSON"),
        ("[1,2,3]", "JSON"),
        ('{"robot_id":1,"robot_id":2}', "JSON"),  # trùng khóa
        (_payload(seq=...), "SCHEMA"),  # thiếu trường
        (_payload(extra=1), "SCHEMA"),  # thừa trường
        (_payload(distance_mm=350.0), "SCHEMA"),  # số thực
        (_payload(estop=True), "SCHEMA"),  # bool
        (_payload(seq=-1), "SCHEMA"),
        (_payload(seq="42"), "SCHEMA"),  # chuỗi
        (_payload(zone=3), "SCHEMA"),
        (_payload(estop=2), "SCHEMA"),
        (_payload(seq=INT63_MAX + 1), "SCHEMA"),
        (_payload(distance_mm=70000), "SCHEMA"),
        (_payload(hmac="abc"), "SCHEMA"),
        (_payload(hmac="g" * 64), "SCHEMA"),
        ('{"seq": NaN}', "SCHEMA"),
        ('{"robot\\u005fid":1}', "JSON"),  # escape bị cấm (chống parser differential với ArduinoJson)
        ("{" + " " * 600 + "}", "SIZE"),
    ],
)
def test_parse_telemetry_rejects(payload, reason):
    with pytest.raises(ProtocolError) as e:
        parse_telemetry(payload)
    assert e.value.reason == reason


def test_canonical_rejects_non_int():
    with pytest.raises(ProtocolError):
        canonical_telemetry(**{**TEL, "distance_mm": 1.5})
    with pytest.raises(ProtocolError):
        canonical_telemetry(**{**TEL, "estop": True})


# ---------------- topic ----------------
@pytest.mark.parametrize(
    "topic,expected",
    [("hrc/1/telemetry", (1, "telemetry")), ("hrc/0/cmd", (0, "cmd")), ("hrc/25/heartbeat", (25, "heartbeat"))],
)
def test_parse_topic_ok(topic, expected):
    assert parse_topic(topic) == expected


@pytest.mark.parametrize(
    "topic", ["hrc/01/telemetry", "hrc/x/telemetry", "hrc/1/telemetry/extra", "abc/1/telemetry", "hrc/1/other",
              "hrc//telemetry", "hrc/99999999999/telemetry"],
)
def test_parse_topic_rejects(topic):
    with pytest.raises(ProtocolError) as e:
        parse_topic(topic)
    assert e.value.reason == "TOPIC"


# ---------------- lệnh ----------------
def test_command_roundtrip_and_vector(key):
    c = build_command(key, 1, "LOCK", 1790000000123)
    assert c.hmac == VECTORS[1][1]
    parsed = parse_command(c.to_json())
    assert parsed == c and verify_command(key, 1, parsed)


def test_command_bound_to_robot_id(key):
    """Lệnh ký cho robot 1 không dùng được cho robot 2 (robot_id nằm trong chuỗi ký)."""
    c = build_command(key, 1, "UNLOCK", 1790000000123)
    assert verify_command(key, 1, c)
    assert not verify_command(key, 2, c)


def test_command_tampered_cmd_rejected(key):
    c = build_command(key, 1, "UNLOCK", 1790000000123)
    assert not verify_command(key, 1, Command("LOCK", c.nonce, c.hmac))
    assert not verify_command(key, 1, Command(c.cmd, c.nonce + 1, c.hmac))


@pytest.mark.parametrize(
    "payload",
    ['{"cmd":"lock","nonce":1,"hmac":"' + "a" * 64 + '"}', '{"cmd":"LOCK","nonce":1}',
     '{"cmd":"REBOOT","nonce":1,"hmac":"' + "a" * 64 + '"}', '{"cmd":"LOCK","nonce":-5,"hmac":"' + "a" * 64 + '"}'],
)
def test_parse_command_rejects(payload):
    with pytest.raises(ProtocolError):
        parse_command(payload)


# ---------------- heartbeat / LWT ----------------
def test_heartbeat_roundtrip_and_vector(key):
    hb = build_heartbeat(key, 1, 1790000000500, 0, 0, "99c3bd15cba71f37")
    assert hb.hmac == VECTORS[2][1]
    parsed = parse_heartbeat(hb.to_json())
    assert isinstance(parsed, Heartbeat) and verify_heartbeat(key, parsed)
    assert not verify_heartbeat(key, replace(parsed, state=1))


def test_lwt_parse():
    assert parse_heartbeat(lwt_payload(7)) == LastWill(7)
    with pytest.raises(ProtocolError):
        parse_heartbeat('{"robot_id":7,"online":1}')


def test_heartbeat_bad_cfg_rejected(key):
    with pytest.raises(ProtocolError):
        build_heartbeat(key, 1, 1, 0, 0, "XYZ")


# ---------------- tách miền (domain separation) ----------------
def test_domain_separation(key):
    """Chữ ký của loại bản tin này không thể là chữ ký hợp lệ của loại khác."""
    t = build_telemetry(key, **TEL)
    c = build_command(key, 1, "LOCK", 42)
    hb = build_heartbeat(key, 1, 42, 0, 0, "0" * 16)
    macs = {t.hmac, c.hmac, hb.hmac}
    assert len(macs) == 3
    assert not verify_command(key, 1, Command("LOCK", 42, t.hmac))
    assert not verify_heartbeat(key, replace(hb, hmac=c.hmac))


# ---------------- khóa ----------------
@pytest.mark.parametrize("bad", ["", "abc", "zz" * 32, "00" * 31, "00" * 32])
def test_load_key_rejects(bad):
    with pytest.raises(ValueError):
        load_key(bad)


def test_load_key_ok():
    assert load_key(bytes(range(32)).hex().upper()) == bytes(range(32))
