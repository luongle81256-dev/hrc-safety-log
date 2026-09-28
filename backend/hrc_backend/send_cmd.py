"""Gửi lệnh LOCK/UNLOCK đã ký HMAC xuống thiết bị (công cụ thử tay; Phase 4 backend tự gọi make_command).

Chạy:  hrc-cmd --robot 1 LOCK
"""
from __future__ import annotations

import argparse
import sys

import paho.mqtt.client as mqtt

from hrc_common.config import load_config, topic
from hrc_common.env import mqtt_settings
from hrc_common.protocol import COMMANDS, Command, build_command

from .ingest import now_ms
from .settings import hmac_key, open_store
from .storage import LogStore


def make_command(store: LogStore, key: bytes, robot_id: int, cmd: str) -> Command:
    """Cấp nonce tăng ngặt (lưu DB) rồi ký lệnh."""
    nonce = store.next_cmd_nonce(robot_id, now_ms(), cmd)
    return build_command(key, robot_id, cmd, nonce)


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Gửi lệnh đã ký xuống robot")
    ap.add_argument("cmd", choices=COMMANDS)
    ap.add_argument("--robot", type=int, default=cfg["system"]["robot_ids"][0])
    args = ap.parse_args(argv)

    try:
        key = hmac_key()
    except ValueError as e:
        print(f"[LỖI] {e}")
        return 2
    store = open_store()
    try:
        c = make_command(store, key, args.robot, args.cmd)
    finally:
        store.close()

    s = mqtt_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hrc-cmd-{c.nonce}")
    if s.username:
        client.username_pw_set(s.username, s.password)
    try:
        client.connect(s.host, s.port, keepalive=cfg["mqtt"]["keepalive_s"])
    except OSError as e:
        print(f"[LỖI] Không kết nối được broker {s.host}:{s.port} – {e}")
        return 1
    client.loop_start()
    info = client.publish(topic("cmd", args.robot), c.to_json(), qos=cfg["mqtt"]["qos_cmd"])
    info.wait_for_publish(5)
    client.loop_stop()
    client.disconnect()
    print(f"[OK] Đã gửi {c.cmd} tới robot {args.robot} (nonce {c.nonce})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
