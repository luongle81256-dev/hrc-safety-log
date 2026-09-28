"""Giao thức bản tin HRC: chuỗi canonical, HMAC-SHA256, parse JSON chặt.

Đặc tả: docs/INTERFACES.md mục 2–5. Firmware C (Phase 5) phải tạo ra ĐÚNG TỪNG BYTE
chuỗi canonical như file này – dùng test vector trong tests/test_protocol.py để đối chiếu.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import re
from dataclasses import asdict, dataclass

UINT16_MAX = 2**16 - 1
UINT32_MAX = 2**32 - 1
UINT64_MAX = 2**64 - 1
# seq / ts_ms / nonce giới hạn 2^63-1 để lưu được vào SQLite INTEGER (có dấu 64 bit)
INT63_MAX = 2**63 - 1
MAX_PAYLOAD_BYTES = 512  # telemetry thật ~150 byte; lớn hơn => loại trước khi parse

CMD_LOCK = "LOCK"
CMD_UNLOCK = "UNLOCK"
COMMANDS = (CMD_LOCK, CMD_UNLOCK)

# Mã lý do loại bản tin (lưu vào DB, hiển thị dashboard)
R_TOPIC = "TOPIC"
R_SIZE = "SIZE"
R_JSON = "JSON"
R_SCHEMA = "SCHEMA"
R_HMAC = "HMAC"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_TOPIC = re.compile(r"hrc/(0|[1-9][0-9]*)/(telemetry|cmd|heartbeat)")


class ProtocolError(ValueError):
    """Bản tin sai định dạng/chữ ký. `reason` là 1 trong các mã R_*."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# =============================================================================
# Khóa & HMAC
# =============================================================================

def load_key(hex_str: str | None = None) -> bytes:
    """Đọc khóa HMAC từ tham số hoặc biến môi trường HRC_HMAC_KEY_HEX (fail-closed)."""
    raw = (hex_str if hex_str is not None else os.environ.get("HRC_HMAC_KEY_HEX", "")).strip().lower()
    if not _HEX64.fullmatch(raw):
        raise ValueError(
            "HRC_HMAC_KEY_HEX phải là 64 ký tự hex (32 byte). "
            'Sinh khóa: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    key = bytes.fromhex(raw)
    if key == bytes(32):
        raise ValueError("HRC_HMAC_KEY_HEX đang là khóa mẫu toàn số 0 – hãy sinh khóa thật")
    return key


def sign(key: bytes, canonical: str) -> str:
    """HMAC-SHA256(key, canonical) dạng hex chữ thường (64 ký tự)."""
    return _hmac.new(key, canonical.encode("ascii"), hashlib.sha256).hexdigest()


def verify(key: bytes, canonical: str, mac_hex: str) -> bool:
    """So sánh constant-time. mac_hex phải là hex chữ thường 64 ký tự."""
    if not isinstance(mac_hex, str) or not _HEX64.fullmatch(mac_hex):
        return False
    return _hmac.compare_digest(sign(key, canonical), mac_hex)


def _join(*parts: object) -> str:
    return "|".join(str(p) for p in parts)


# =============================================================================
# Parse JSON chặt
# =============================================================================

def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for k, v in pairs:
        if k in obj:
            # Trùng khóa là nguy hiểm: parser C (ArduinoJson) và Python có thể lấy giá trị khác nhau
            raise ProtocolError(R_JSON, f"trùng khóa '{k}'")
        obj[k] = v
    return obj


def _reject_float(s: str):
    raise ProtocolError(R_SCHEMA, f"không chấp nhận số thực '{s}'")


def _reject_constant(s: str):
    raise ProtocolError(R_SCHEMA, f"không chấp nhận hằng '{s}'")


def loads_strict(payload: bytes | str) -> dict:
    """JSON -> dict. Chặn: payload quá lớn, không phải UTF-8, trùng khóa, số thực, NaN/Infinity."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ProtocolError(R_SIZE, f"payload {len(raw)} byte > {MAX_PAYLOAD_BYTES}")
    if b"\\" in raw:
        # Không bản tin hợp lệ nào cần escape. Cấm để mọi parser (Python, ArduinoJson trên ESP32) hiểu GIỐNG NHAU
        # – chặn kiểu viết khóa "cmd" lách kiểm tra trùng khóa ở phía firmware.
        raise ProtocolError(R_JSON, "không cho phép ký tự escape '\\'")
    try:
        text = raw.decode("utf-8")
        obj = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError(R_JSON, str(e)) from e
    if not isinstance(obj, dict):
        raise ProtocolError(R_JSON, "payload phải là JSON object")
    return obj


def _check_exact_keys(obj: dict, expected: set[str]) -> None:
    missing, extra = expected - obj.keys(), obj.keys() - expected
    if missing or extra:
        raise ProtocolError(R_SCHEMA, f"thiếu {sorted(missing)}, thừa {sorted(extra)}")


def _uint(obj: dict, name: str, max_value: int) -> int:
    v = obj[name]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ProtocolError(R_SCHEMA, f"'{name}' phải là số nguyên, nhận {v!r}")
    if not 0 <= v <= max_value:
        raise ProtocolError(R_SCHEMA, f"'{name}'={v} ngoài khoảng [0, {max_value}]")
    return v


def _mac(obj: dict) -> str:
    v = obj["hmac"]
    if not isinstance(v, str) or not _HEX64.fullmatch(v):
        raise ProtocolError(R_SCHEMA, "'hmac' phải là 64 ký tự hex chữ thường")
    return v


def _dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))


# =============================================================================
# Topic
# =============================================================================

def parse_topic(topic: str) -> tuple[int, str]:
    """'hrc/1/telemetry' -> (1, 'telemetry'). robot_id không được có số 0 ở đầu."""
    m = _TOPIC.fullmatch(topic)
    if not m:
        raise ProtocolError(R_TOPIC, f"topic không hợp lệ: {topic!r}")
    robot_id = int(m.group(1))
    if robot_id > UINT32_MAX:
        raise ProtocolError(R_TOPIC, f"robot_id quá lớn: {robot_id}")
    return robot_id, m.group(2)


# =============================================================================
# Telemetry – mục 3
# =============================================================================

# Giới hạn từng trường (khớp kiểu dữ liệu trên firmware)
TELEMETRY_LIMITS = {
    "robot_id": UINT32_MAX,
    "seq": INT63_MAX,
    "ts_ms": INT63_MAX,
    "distance_mm": UINT16_MAX,
    "v_robot_mmps": UINT16_MAX,
    "zone": 2,
    "estop": 1,
}
TELEMETRY_FIELDS = tuple(TELEMETRY_LIMITS)


@dataclass(frozen=True, slots=True)
class Telemetry:
    robot_id: int
    seq: int
    ts_ms: int
    distance_mm: int
    v_robot_mmps: int
    zone: int
    estop: int
    hmac: str = ""

    def values(self) -> tuple[int, ...]:
        """7 trường theo đúng thứ tự canonical / leaf Merkle."""
        return (self.robot_id, self.seq, self.ts_ms, self.distance_mm, self.v_robot_mmps, self.zone, self.estop)

    def canonical(self) -> str:
        return _join(*self.values())

    def to_json(self) -> str:
        return _dumps(asdict(self))


def canonical_telemetry(robot_id: int, seq: int, ts_ms: int, distance_mm: int,
                        v_robot_mmps: int, zone: int, estop: int) -> str:
    """'robot_id|seq|ts_ms|distance_mm|v_robot_mmps|zone|estop'"""
    obj = dict(zip(TELEMETRY_FIELDS, (robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop)))
    return _join(*(_uint(obj, k, lim) for k, lim in TELEMETRY_LIMITS.items()))


def build_telemetry(key: bytes, **fields: int) -> Telemetry:
    """Tạo telemetry đã ký (dùng cho simulator và test)."""
    canonical = canonical_telemetry(**fields)
    return Telemetry(**fields, hmac=sign(key, canonical))


def parse_telemetry(payload: bytes | str) -> Telemetry:
    """Kiểm tra schema (CHƯA kiểm HMAC – gọi verify_telemetry riêng để có mã lý do rõ ràng)."""
    obj = loads_strict(payload)
    _check_exact_keys(obj, set(TELEMETRY_FIELDS) | {"hmac"})
    values = {k: _uint(obj, k, lim) for k, lim in TELEMETRY_LIMITS.items()}
    return Telemetry(**values, hmac=_mac(obj))


def verify_telemetry(key: bytes, t: Telemetry) -> bool:
    return verify(key, t.canonical(), t.hmac)


# =============================================================================
# Lệnh – mục 4
# =============================================================================

@dataclass(frozen=True, slots=True)
class Command:
    cmd: str
    nonce: int
    hmac: str = ""

    def to_json(self) -> str:
        return _dumps(asdict(self))


def canonical_command(robot_id: int, cmd: str, nonce: int) -> str:
    """'robot_id|cmd|nonce' – robot_id lấy từ topic, KHÔNG nằm trong payload."""
    if cmd not in COMMANDS:
        raise ProtocolError(R_SCHEMA, f"cmd phải thuộc {COMMANDS}, nhận {cmd!r}")
    obj = {"robot_id": robot_id, "nonce": nonce}
    return _join(_uint(obj, "robot_id", UINT32_MAX), cmd, _uint(obj, "nonce", INT63_MAX))


def build_command(key: bytes, robot_id: int, cmd: str, nonce: int) -> Command:
    return Command(cmd=cmd, nonce=nonce, hmac=sign(key, canonical_command(robot_id, cmd, nonce)))


def parse_command(payload: bytes | str) -> Command:
    obj = loads_strict(payload)
    _check_exact_keys(obj, {"cmd", "nonce", "hmac"})
    cmd = obj["cmd"]
    if cmd not in COMMANDS:
        raise ProtocolError(R_SCHEMA, f"cmd phải thuộc {COMMANDS}, nhận {cmd!r}")
    return Command(cmd=cmd, nonce=_uint(obj, "nonce", INT63_MAX), hmac=_mac(obj))


def verify_command(key: bytes, robot_id: int, c: Command) -> bool:
    return verify(key, canonical_command(robot_id, c.cmd, c.nonce), c.hmac)


# =============================================================================
# Heartbeat & LWT – mục 5
# =============================================================================

@dataclass(frozen=True, slots=True)
class Heartbeat:
    robot_id: int
    ts_ms: int
    state: int  # 0 = ACTIVE, 1 = LOCKED (tại thiết bị)
    estop: int
    cfg: str  # HRC_CONFIG_HASH (16 hex)
    hmac: str = ""

    def canonical(self) -> str:
        return canonical_heartbeat(self.robot_id, self.ts_ms, self.state, self.estop, self.cfg)

    def to_json(self) -> str:
        return _dumps(asdict(self))


@dataclass(frozen=True, slots=True)
class LastWill:
    """Bản tin LWT do broker phát khi thiết bị mất kết nối bất thường (không ký)."""

    robot_id: int


def canonical_heartbeat(robot_id: int, ts_ms: int, state: int, estop: int, cfg: str) -> str:
    """'hb|robot_id|ts_ms|state|estop|cfg' – tiền tố 'hb' để tách miền với telemetry/lệnh."""
    obj = {"robot_id": robot_id, "ts_ms": ts_ms, "state": state, "estop": estop}
    if not isinstance(cfg, str) or not _HEX16.fullmatch(cfg):
        raise ProtocolError(R_SCHEMA, "'cfg' phải là 16 ký tự hex chữ thường")
    return _join(
        "hb",
        _uint(obj, "robot_id", UINT32_MAX),
        _uint(obj, "ts_ms", INT63_MAX),
        _uint(obj, "state", 1),
        _uint(obj, "estop", 1),
        cfg,
    )


def build_heartbeat(key: bytes, robot_id: int, ts_ms: int, state: int, estop: int, cfg: str) -> Heartbeat:
    return Heartbeat(robot_id, ts_ms, state, estop, cfg, sign(key, canonical_heartbeat(robot_id, ts_ms, state, estop, cfg)))


def lwt_payload(robot_id: int) -> str:
    return _dumps({"robot_id": robot_id, "online": 0})


def parse_heartbeat(payload: bytes | str) -> Heartbeat | LastWill:
    obj = loads_strict(payload)
    if obj.keys() == {"robot_id", "online"}:
        if _uint(obj, "online", 0) != 0:
            raise ProtocolError(R_SCHEMA, "LWT phải có online = 0")
        return LastWill(_uint(obj, "robot_id", UINT32_MAX))
    _check_exact_keys(obj, {"robot_id", "ts_ms", "state", "estop", "cfg", "hmac"})
    cfg = obj["cfg"]
    if not isinstance(cfg, str) or not _HEX16.fullmatch(cfg):
        raise ProtocolError(R_SCHEMA, "'cfg' phải là 16 ký tự hex chữ thường")
    return Heartbeat(
        robot_id=_uint(obj, "robot_id", UINT32_MAX),
        ts_ms=_uint(obj, "ts_ms", INT63_MAX),
        state=_uint(obj, "state", 1),
        estop=_uint(obj, "estop", 1),
        cfg=cfg,
        hmac=_mac(obj),
    )


def verify_heartbeat(key: bytes, hb: Heartbeat) -> bool:
    return verify(key, hb.canonical(), hb.hmac)
