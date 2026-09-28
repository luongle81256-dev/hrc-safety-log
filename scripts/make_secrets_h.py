"""Sinh iot_code/firmware/include/secrets.h từ .env – khóa HMAC của firmware luôn TRÙNG backend.

Chạy:  python scripts/make_secrets_h.py --ssid "TenWifi" --password "MatKhau"
       (MQTT_HOST mặc định = IP LAN của máy này; ESP32 KHÔNG dùng được "localhost")
secrets.h đã có trong .gitignore – KHÔNG commit.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hrc_common.env import load_env  # noqa: E402
from hrc_common.protocol import load_key  # noqa: E402

OUT = ROOT / "iot_code" / "firmware" / "include" / "secrets.h"


def lan_ip() -> str:
    """IP LAN của máy (mở socket UDP tới 1 địa chỉ ngoài – không gửi gói nào)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
        except OSError:
            return "192.168.1.100"


def c_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ssid", default="ten-wifi")
    ap.add_argument("--password", default="mat-khau-wifi")
    ap.add_argument("--mqtt-host", default=None, help="mặc định: IP LAN của máy này")
    args = ap.parse_args()
    try:
        key = load_key()
    except ValueError as e:
        print(f"[LỖI] {e}")
        return 2
    host = args.mqtt_host or lan_ip()
    OUT.write_text(
        "// TỰ SINH bởi scripts/make_secrets_h.py từ .env – KHÔNG COMMIT (đã .gitignore)\n"
        "#pragma once\n\n"
        f"#define WIFI_SSID       {c_str(args.ssid)}\n"
        f"#define WIFI_PASSWORD   {c_str(args.password)}\n\n"
        f"#define MQTT_HOST       {c_str(host)}\n"
        f"#define MQTT_PORT       {int(os.getenv('MQTT_PORT', '1883'))}\n"
        f"#define MQTT_USER       {c_str(os.getenv('MQTT_USERNAME', ''))}\n"
        f"#define MQTT_PASS       {c_str(os.getenv('MQTT_PASSWORD', ''))}\n\n"
        f"#define HRC_HMAC_KEY_HEX \"{key.hex()}\"\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"[OK] Đã ghi {OUT.relative_to(ROOT)} (MQTT_HOST={host}, SSID={args.ssid})")
    if args.ssid == "ten-wifi":
        print("     Nhớ chạy lại với --ssid/--password thật trước khi nạp vào ESP32.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
