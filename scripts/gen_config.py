"""Sinh file cấu hình cho các module không đọc được TOML từ config/hrc_config.toml.

Sinh ra:
    iot_code/firmware/include/hrc_params.h   (firmware C)
    contracts/hrc_params.json                (Ignition module + test Hardhat)

Cách dùng:
    python scripts/gen_config.py          # ghi các file trên
    python scripts/gen_config.py --check  # chỉ kiểm tra có khớp config không (exit 1 nếu lệch)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # chạy được cả khi chưa `pip install -e .`

from hrc_common.config import config_hash, load_config, ssm_params  # noqa: E402

HEADER_PATH = ROOT / "iot_code" / "firmware" / "include" / "hrc_params.h"
CONTRACTS_JSON_PATH = ROOT / "contracts" / "hrc_params.json"


def _topic_fmt(template: str) -> str:
    """'hrc/{robot_id}/telemetry' -> 'hrc/%u/telemetry' (dùng với snprintf)."""
    return template.replace("{robot_id}", "%u")


def render_contracts_json() -> str:
    cfg = load_config()
    data = {
        "_note": "TỰ SINH từ config/hrc_config.toml bởi scripts/gen_config.py – KHÔNG SỬA TAY",
        "configHash": config_hash(),
        "robotIds": cfg["system"]["robot_ids"],
        "maxViolations": cfg["chain"]["max_violations"],
        "batchMaxRecords": cfg["merkle"]["batch_max_records"],
        "telemetryPeriodMs": cfg["system"]["telemetry_period_ms"],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def render_header() -> str:
    cfg = load_config()
    profile = cfg["ssm"]["active_profile"]
    p = ssm_params()
    sysc, mq, mk, ch = cfg["system"], cfg["mqtt"], cfg["merkle"], cfg["chain"]

    lines = [
        "// =============================================================================",
        "// TỰ SINH từ config/hrc_config.toml bởi scripts/gen_config.py – KHÔNG SỬA TAY.",
        "// Sửa config rồi chạy lại: python scripts/gen_config.py",
        "// =============================================================================",
        "#pragma once",
        "#include <stdint.h>",
        "",
        f'#define HRC_CONFIG_HASH "{config_hash()}"   // so khớp với backend để phát hiện lệch cấu hình',
        f'#define HRC_SSM_PROFILE "{profile}"',
        "",
        "// ---- [system] ----",
        f"#define HRC_TELEMETRY_PERIOD_MS {sysc['telemetry_period_ms']}u",
        f"#define HRC_HEARTBEAT_PERIOD_MS {sysc['heartbeat_period_ms']}u",
        f"#define HRC_FAILSAFE_TIMEOUT_MS {sysc['failsafe_timeout_ms']}u",
        f"#define HRC_SEQ_RESERVE_BLOCK   {sysc['seq_reserve_block']}u",
        "",
        "// ---- [fastlane] / [security] ----",
        f"#define HRC_ESTOP_RESET_HOLD_MS   {cfg['fastlane']['estop_reset_hold_ms']}u",
        f"#define HRC_WARNING_SPEED_PERCENT {cfg['fastlane']['warning_speed_percent']}u",
        f"#define HRC_CMD_MAX_SKEW_MS       {cfg['security']['cmd_max_skew_ms']}u",
        "",
        "// ---- [mqtt] ---- (dùng với snprintf(buf, n, FMT, (unsigned)robot_id))",
        f'#define HRC_TOPIC_TELEMETRY_FMT "{_topic_fmt(mq["topic_telemetry"])}"',
        f'#define HRC_TOPIC_CMD_FMT       "{_topic_fmt(mq["topic_cmd"])}"',
        f'#define HRC_TOPIC_HEARTBEAT_FMT "{_topic_fmt(mq["topic_heartbeat"])}"',
        f"#define HRC_MQTT_QOS_CMD        {mq['qos_cmd']}",
        f"#define HRC_MQTT_KEEPALIVE_S    {mq['keepalive_s']}",
        "",
        "// ---- [zones] ---- (interface đã chốt)",
    ]
    lines += [f"#define HRC_ZONE_{name} {code}" for name, code in cfg["zones"].items()]
    lines += ["", f"// ---- [ssm.profiles.{profile}] ----"]
    lines += [f"#define HRC_SSM_{k.upper()} {v}u" for k, v in asdict(p).items()]
    lines += [
        "",
        "// ---- [merkle] / [chain] ---- (firmware không dùng, để tham chiếu)",
        f"#define HRC_MERKLE_BATCH_MAX_RECORDS {mk['batch_max_records']}u",
        f"#define HRC_MAX_VIOLATIONS {ch['max_violations']}u",
        "",
        "// S = ceil((vH*(TR+TS) + vR*TR)/1000) + SS + C + ZR + ZS  (mm)",
        "// PHẢI giống hrc_common/ssm.py. Chặn vR để không tràn uint32.",
        "static inline uint32_t hrc_ssm_protective_distance_mm(uint32_t v_robot_mmps) {",
        "    if (v_robot_mmps > 100000u) v_robot_mmps = 100000u;",
        "    uint32_t dyn = HRC_SSM_V_HUMAN_MMPS * (HRC_SSM_T_REACT_MS + HRC_SSM_T_STOP_MS)",
        "                 + v_robot_mmps * HRC_SSM_T_REACT_MS;",
        "    return (dyn + 999u) / 1000u + HRC_SSM_S_STOP_MM + HRC_SSM_C_INTRUSION_MM",
        "           + HRC_SSM_Z_ROBOT_MM + HRC_SSM_Z_SENSOR_MM;",
        "}",
        "",
        "// Biên (d == S) tính là vùng nguy hiểm hơn.",
        "static inline uint8_t hrc_ssm_classify_zone(uint32_t distance_mm, uint32_t v_robot_mmps) {",
        "    uint32_t s = hrc_ssm_protective_distance_mm(v_robot_mmps);",
        "    if (distance_mm <= s) return HRC_ZONE_DANGER;",
        "    if (distance_mm <= s + HRC_SSM_WARNING_MARGIN_MM) return HRC_ZONE_WARNING;",
        "    return HRC_ZONE_SAFE;",
        "}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="chỉ kiểm tra, không ghi file")
    args = ap.parse_args()

    outputs = {HEADER_PATH: render_header(), CONTRACTS_JSON_PATH: render_contracts_json()}
    if args.check:
        ok = True
        for path, content in outputs.items():
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current.replace("\r\n", "\n") != content:
                print(f"[LỆCH] {path.relative_to(ROOT)} không khớp config. Chạy: python scripts/gen_config.py")
                ok = False
            else:
                print(f"[OK] {path.relative_to(ROOT)} khớp config (hash {config_hash()})")
        return 0 if ok else 1

    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"[OK] Đã ghi {path.relative_to(ROOT)} (hash {config_hash()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
