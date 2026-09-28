"""Nạp và kiểm tra file cấu hình dùng chung config/hrc_config.toml.

Chỉ dùng thư viện chuẩn (tomllib, Python >= 3.11) để script sinh header
chạy được mà không cần cài thêm gì.
"""
from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "hrc_config.toml"

# Mã vùng theo interface đã chốt – dùng để đối chiếu với file config.
ZONE_SAFE = 0
ZONE_WARNING = 1
ZONE_DANGER = 2


class ConfigError(ValueError):
    """Cấu hình sai hoặc thiếu."""


@dataclass(frozen=True)
class SSMParams:
    """Tham số SSM của 1 profile (mm, ms, mm/s – số nguyên)."""

    v_human_mmps: int
    t_react_ms: int
    t_stop_ms: int
    s_stop_mm: int
    c_intrusion_mm: int
    z_robot_mm: int
    z_sensor_mm: int
    warning_margin_mm: int
    v_robot_nominal_mmps: int
    v_robot_limit_mmps: int
    distance_valid_min_mm: int
    distance_valid_max_mm: int


def config_path() -> Path:
    """Cho phép ghi đè đường dẫn bằng biến môi trường HRC_CONFIG_PATH (dùng khi test)."""
    return Path(os.environ.get("HRC_CONFIG_PATH", DEFAULT_CONFIG_PATH))


def config_hash(path: Path | None = None) -> str:
    """SHA-256 (16 hex đầu) của file config, đã chuẩn hóa xuống dòng về LF.

    Firmware và backend cùng mang giá trị này để phát hiện lệch cấu hình.
    """
    raw = (path or config_path()).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()[:16]


def _require_uint(name: str, value: object) -> int:
    # bool là con của int trong Python nên phải loại riêng
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"'{name}' phải là số nguyên >= 0, nhận được {value!r}")
    return value


def _validate(cfg: dict) -> None:
    zones = cfg["zones"]
    expected = {"SAFE": ZONE_SAFE, "WARNING": ZONE_WARNING, "DANGER": ZONE_DANGER}
    if zones != expected:
        raise ConfigError(f"[zones] phải đúng interface đã chốt {expected}, nhận được {zones}")

    for key in ("topic_telemetry", "topic_cmd", "topic_heartbeat"):
        if "{robot_id}" not in cfg["mqtt"][key]:
            raise ConfigError(f"[mqtt].{key} phải chứa '{{robot_id}}'")

    for rid in cfg["system"]["robot_ids"]:
        _require_uint("robot_ids[]", rid)

    profiles = cfg["ssm"]["profiles"]
    active = cfg["ssm"]["active_profile"]
    if active not in profiles:
        raise ConfigError(f"active_profile '{active}' không có trong [ssm.profiles]")

    names = {f.name for f in fields(SSMParams)}
    for pname, prof in profiles.items():
        missing, extra = names - prof.keys(), prof.keys() - names
        if missing or extra:
            raise ConfigError(f"profile '{pname}': thiếu {sorted(missing)}, thừa {sorted(extra)}")
        for k, v in prof.items():
            _require_uint(f"{pname}.{k}", v)
        if prof["distance_valid_min_mm"] >= prof["distance_valid_max_mm"]:
            raise ConfigError(f"profile '{pname}': distance_valid_min_mm phải < distance_valid_max_mm")

    for section, keys in {
        "system": ("telemetry_period_ms", "heartbeat_period_ms", "failsafe_timeout_ms", "seq_reserve_block"),
        "fastlane": ("estop_reset_hold_ms", "warning_speed_percent"),
        "security": ("cmd_max_skew_ms", "ts_max_future_skew_ms", "rejected_payload_max_bytes"),
        "merkle": ("batch_max_records", "batch_max_interval_ms"),
        "chain": ("max_violations", "listener_poll_ms", "tx_timeout_s"),
        "incident": ("intrusion_min_consecutive", "overspeed_min_consecutive", "sensor_fault_min_consecutive",
                     "severity_intrusion", "severity_intrusion_no_estop", "severity_overspeed",
                     "severity_sensor_fault"),
    }.items():
        for k in keys:
            if _require_uint(f"{section}.{k}", cfg[section][k]) == 0:
                raise ConfigError(f"'{section}.{k}' phải > 0")

    if cfg["system"]["failsafe_timeout_ms"] <= cfg["system"]["heartbeat_period_ms"]:
        raise ConfigError("failsafe_timeout_ms phải lớn hơn heartbeat_period_ms")
    if cfg["fastlane"]["warning_speed_percent"] > 100:
        raise ConfigError("warning_speed_percent phải trong (0, 100]")
    for k, v in cfg["incident"].items():
        if k.startswith("severity_") and not 1 <= v <= 3:
            raise ConfigError(f"[incident].{k} phải trong [1, 3] (MIN/MAX_SEVERITY của contract)")


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Đọc + kiểm tra config. Kết quả được cache cho cả tiến trình."""
    path = config_path()
    try:
        with path.open("rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"Không tìm thấy file cấu hình: {path}") from e
    try:
        _validate(cfg)
    except KeyError as e:
        raise ConfigError(f"Thiếu khóa cấu hình: {e}") from e
    return cfg


def ssm_params(profile: str | None = None) -> SSMParams:
    """Trả về tham số SSM của profile (mặc định: active_profile)."""
    cfg = load_config()
    name = profile or cfg["ssm"]["active_profile"]
    try:
        return SSMParams(**cfg["ssm"]["profiles"][name])
    except KeyError as e:
        raise ConfigError(f"Không có SSM profile '{name}'") from e


def topic(kind: str, robot_id: int) -> str:
    """Tạo topic MQTT: kind in {'telemetry', 'cmd', 'heartbeat'}."""
    template = load_config()["mqtt"].get(f"topic_{kind}")
    if template is None:
        raise ConfigError(f"Không có topic loại '{kind}'")
    return template.format(robot_id=int(robot_id))
