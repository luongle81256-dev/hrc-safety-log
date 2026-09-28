"""Simulator ESP32 chạy thật qua MQTT (dự phòng khi không có phần cứng).

Ví dụ:
    hrc-sim --scenario approach --duration 60
    hrc-sim --scenario mixed --attack replay --attack-every 30
    hrc-sim --scenario sensor_noise --labels-out ai_model/data/raw/sim_labels.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import paho.mqtt.client as mqtt

from hrc_common.config import ROOT_DIR, load_config, topic
from hrc_common.env import load_env, mqtt_settings
from hrc_common.protocol import Telemetry, load_key, lwt_payload

from .device import CMD_OK, FileNvs, MemoryNvs, SimDevice
from .scenarios import SCENARIOS, make_scenario

log = logging.getLogger("hrc-sim")
ZONE_NAMES = ("SAFE", "WARNING", "DANGER")


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def main(argv: list[str] | None = None) -> int:
    load_env()
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Simulator ESP32 – HRC Safety Log")
    ap.add_argument("--robot-id", type=int, default=cfg["system"]["robot_ids"][0])
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="mixed")
    ap.add_argument("--duration", type=float, default=0, help="giây chạy (0 = chạy mãi, Ctrl+C để dừng)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--state-dir", type=Path, default=ROOT_DIR / "iot_code" / "simulator" / ".state",
                    help="thư mục giả lập NVS (seq, last_nonce, locked)")
    ap.add_argument("--no-nvs", action="store_true", help="không lưu NVS (mỗi lần chạy như chip mới)")
    ap.add_argument("--attack", choices=("none", "forge", "replay"), default="none",
                    help="forge: sửa distance giữ nguyên hmac; replay: phát lại bản tin cũ")
    ap.add_argument("--attack-every", type=int, default=50, help="mỗi N bản tin chèn 1 tấn công")
    ap.add_argument("--labels-out", type=Path, help="ghi CSV nhãn đúng (cho AI) ra file này")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    try:
        key = load_key()
    except ValueError as e:
        log.error("%s", e)
        return 2

    nvs = MemoryNvs() if args.no_nvs else FileNvs(args.state_dir / f"robot_{args.robot_id}.json")
    device = SimDevice(args.robot_id, key, nvs)
    period_ms = cfg["system"]["telemetry_period_ms"]
    hb_period_ms = cfg["system"]["heartbeat_period_ms"]
    failsafe_ms = cfg["system"]["failsafe_timeout_ms"]
    scenario = make_scenario(args.scenario, args.seed, period_ms=period_ms)
    lock = threading.Lock()  # callback MQTT chạy ở thread khác vòng lặp chính

    t_tel, t_cmd, t_hb = (topic(k, args.robot_id) for k in ("telemetry", "cmd", "heartbeat"))
    s = mqtt_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hrc-sim-{args.robot_id}")
    if s.username:
        client.username_pw_set(s.username, s.password)
    client.will_set(t_hb, lwt_payload(args.robot_id), qos=0, retain=False)

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Broker từ chối kết nối: %s", reason_code)
            return
        c.subscribe(t_cmd, qos=cfg["mqtt"]["qos_cmd"])
        log.info("Đã kết nối %s:%s, nghe lệnh ở '%s'", s.host, s.port, t_cmd)

    def on_message(c, userdata, msg):
        with lock:
            result = device.handle_command(msg.payload, _now_ms())
            locked = device.locked
        if result == CMD_OK:
            log.warning(">>> NHẬN LỆNH HỢP LỆ – robot %s", "LOCKED" if locked else "đã gỡ khóa (chờ SAFE)")
        else:
            log.warning(">>> TỪ CHỐI LỆNH: %s", result)

    client.on_connect, client.on_message = on_connect, on_message
    client.connect_async(s.host, s.port, keepalive=cfg["mqtt"]["keepalive_s"])
    client.loop_start()

    label_file = label_writer = None
    if args.labels_out:
        args.labels_out.parent.mkdir(parents=True, exist_ok=True)
        label_file = args.labels_out.open("w", newline="", encoding="utf-8")
        label_writer = csv.writer(label_file)
        label_writer.writerow(["robot_id", "seq", "ts_ms", "true_distance_mm", "measured_distance_mm",
                               "v_cmd_mmps", "v_robot_mmps", "zone", "estop", "fault", "label"])

    log.info("Kịch bản '%s' (seed %d), robot %d, chu kỳ %d ms, attack=%s",
             args.scenario, args.seed, args.robot_id, period_ms, args.attack)
    sent = dropped = attacks = 0
    history: list[Telemetry] = []
    start = time.monotonic()
    next_hb = 0.0
    disconnected_since: float | None = None
    i = 0
    try:
        while args.duration <= 0 or time.monotonic() - start < args.duration:
            # giữ nhịp chu kỳ cố định (không trôi)
            target = start + i * period_ms / 1000
            time.sleep(max(0.0, target - time.monotonic()))
            now = _now_ms()
            sample = scenario.step()
            with lock:
                # Fail-safe: mất MQTT quá lâu => ngắt relay (giống firmware)
                if client.is_connected():
                    disconnected_since = None
                else:
                    disconnected_since = disconnected_since or time.monotonic()
                    if (time.monotonic() - disconnected_since) * 1000 > failsafe_ms:
                        device.force_estop("MQTT_LOST")
                tel = device.tick(now, sample.measured_distance_mm, sample.v_cmd_mmps)
                hb = device.heartbeat(now) if time.monotonic() >= next_hb else None
                estop_reason = device.estop_reason

            if client.is_connected():
                client.publish(t_tel, tel.to_json(), qos=cfg["mqtt"]["qos_telemetry"])
                sent += 1
                if hb:
                    client.publish(t_hb, hb.to_json(), qos=cfg["mqtt"]["qos_heartbeat"])
                    next_hb = time.monotonic() + hb_period_ms / 1000
                if args.attack != "none" and sent % args.attack_every == 0 and history:
                    attacks += 1
                    if args.attack == "forge":
                        # Kẻ tấn công sửa khoảng cách thành "an toàn" nhưng không có khóa để ký lại
                        forged = replace(tel, distance_mm=tel.distance_mm + 500)
                        client.publish(t_tel, forged.to_json(), qos=0)
                        log.warning("[ATTACK] forge: gửi seq %d với distance bị sửa", tel.seq)
                    else:
                        old = history[max(0, len(history) - 20)]
                        client.publish(t_tel, old.to_json(), qos=0)
                        log.warning("[ATTACK] replay: phát lại bản tin seq %d", old.seq)
            else:
                dropped += 1
            history = (history + [tel])[-50:]

            if label_writer:
                label_writer.writerow([tel.robot_id, tel.seq, tel.ts_ms, sample.true_distance_mm, tel.distance_mm,
                                       sample.v_cmd_mmps, tel.v_robot_mmps, tel.zone, tel.estop,
                                       sample.fault or "", sample.label])
            if i % 10 == 0:
                log.info("seq=%-7d d=%4dmm v=%3dmm/s %-7s estop=%d%s | %s",
                         tel.seq, tel.distance_mm, tel.v_robot_mmps, ZONE_NAMES[tel.zone], tel.estop,
                         f"({estop_reason})" if tel.estop else "", sample.label)
            i += 1
    except KeyboardInterrupt:
        log.info("Dừng theo yêu cầu (Ctrl+C)")
    finally:
        # disconnect() "sạch" => broker KHÔNG phát LWT; LWT chỉ dành cho mất kết nối bất thường
        client.loop_stop()
        client.disconnect()
        if label_file:
            label_file.close()
    log.info("Tổng kết: gửi %d, bỏ (mất kết nối) %d, tấn công giả %d", sent, dropped, attacks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
