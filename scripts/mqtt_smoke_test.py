"""Nghiệm thu Phase 0: kiểm tra broker Mosquitto publish/subscribe được.

Luồng: connect -> subscribe topic thử -> publish 1 gói telemetry mẫu -> chờ nhận lại
-> so khớp nội dung -> in độ trễ vòng (RTT). Exit code 0 = PASS, 1 = FAIL.

Cách dùng:
    python scripts/mqtt_smoke_test.py            # đọc MQTT_HOST/MQTT_PORT từ .env
    python scripts/mqtt_smoke_test.py --host 192.168.1.10 --port 1883
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hrc_common.config import load_config, topic  # noqa: E402
from hrc_common.ssm import classify_zone  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="MQTT smoke test (Phase 0)")
    ap.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    ap.add_argument("--timeout", type=float, default=5.0, help="giây chờ mỗi bước")
    args = ap.parse_args()

    cfg = load_config()
    robot_id = cfg["system"]["robot_ids"][0]
    run_id = uuid.uuid4().hex[:8]
    # Topic riêng cho smoke test để không lẫn vào log thật của hrc/{robot_id}/telemetry
    test_topic = f"hrc/_smoke/{run_id}"

    # Gói mẫu đúng schema telemetry (CHƯA ký HMAC – HMAC làm ở Phase 2)
    distance, v_robot = 400, cfg["ssm"]["profiles"][cfg["ssm"]["active_profile"]]["v_robot_nominal_mmps"]
    sample = {
        "robot_id": robot_id, "seq": 1, "ts_ms": int(time.time() * 1000),
        "distance_mm": distance, "v_robot_mmps": v_robot,
        "zone": classify_zone(distance, v_robot), "estop": 0, "hmac": "",
    }
    payload = json.dumps(sample, separators=(",", ":"))

    subscribed, received = threading.Event(), threading.Event()
    result: dict = {}

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[FAIL] Broker từ chối kết nối: {reason_code}")
            return
        print(f"[..] Đã kết nối {args.host}:{args.port}, subscribe '{test_topic}'")
        client.subscribe(test_topic, qos=1)

    def on_subscribe(client, userdata, mid, reason_code_list, properties):
        if reason_code_list and not reason_code_list[0].is_failure:
            subscribed.set()
        else:
            print(f"[FAIL] Subscribe bị từ chối: {reason_code_list}")

    def on_message(client, userdata, msg):
        result["payload"] = msg.payload.decode("utf-8")
        result["t_recv"] = time.perf_counter()
        received.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hrc-smoke-{run_id}")
    if os.getenv("MQTT_USERNAME"):
        client.username_pw_set(os.getenv("MQTT_USERNAME"), os.getenv("MQTT_PASSWORD") or None)
    client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message

    try:
        client.connect(args.host, args.port, keepalive=cfg["mqtt"]["keepalive_s"])
    except OSError as e:
        print(f"[FAIL] Không kết nối được {args.host}:{args.port} – {e}")
        print("       Broker đã chạy chưa? Thử: docker compose up -d mosquitto")
        return 1

    client.loop_start()
    try:
        if not subscribed.wait(args.timeout):
            print("[FAIL] Hết thời gian chờ subscribe")
            return 1
        t_pub = time.perf_counter()
        client.publish(test_topic, payload, qos=1).wait_for_publish(args.timeout)
        if not received.wait(args.timeout):
            print("[FAIL] Không nhận lại được message đã publish")
            return 1
        if result["payload"] != payload:
            print(f"[FAIL] Nội dung sai lệch:\n  gửi: {payload}\n  nhận: {result['payload']}")
            return 1
        rtt_ms = (result["t_recv"] - t_pub) * 1000
        print(f"[OK] Pub/Sub thành công, RTT = {rtt_ms:.1f} ms")
        print(f"     Topic thật của robot {robot_id}: {topic('telemetry', robot_id)} | "
              f"{topic('cmd', robot_id)} | {topic('heartbeat', robot_id)}")
        print(f"     Payload: {payload}")
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    sys.exit(main())
