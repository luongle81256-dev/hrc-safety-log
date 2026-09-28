"""Backend đầy đủ (trust lane): MQTT ingest -> AI -> chính sách sự cố -> chuỗi; batch Merkle; nghe event -> lệnh.

Luồng xử lý:
    MQTT thread     : nhận telemetry/heartbeat -> kiểm tra -> SQLite -> AI -> (sự cố) đưa vào hàng đợi
    chain-writer    : 1 LUỒNG DUY NHẤT gửi giao dịch (nonce tuần tự). Ưu tiên: sự cố (0) > batch (1)
    chain-listener  : poll event: EmergencyStop -> gửi LOCK, Unlocked -> gửi UNLOCK
    luồng chính     : timeout heartbeat, hẹn giờ batch, in thống kê

    uvicorn (luồng chính): API REST + WebSocket cho dashboard (backend/hrc_backend/api.py)

Chạy:  hrc-backend      (cần: Mosquitto, Hardhat node + contract đã deploy, .env có DEVICE_PRIVATE_KEY)
"""
from __future__ import annotations

import argparse
import itertools
import logging
import os
import queue
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import paho.mqtt.client as mqtt
import uvicorn

from hrc_ai.features import WINDOW
from hrc_ai.inference import HrcDetector
from hrc_common.config import load_config, ssm_params, topic
from hrc_common.env import MqttSettings, load_env, mqtt_settings, network
from hrc_common.protocol import CMD_LOCK, CMD_UNLOCK, Telemetry
from hrc_common.ssm import protective_distance_mm

from .api import create_app
from .batcher import MerkleBatcher
from .hub import EventHub
from .chain import STATE_ACTIVE, STATE_LOCKED, ZERO_ADDRESS, ChainClient, ChainError
from .incidents import IncidentPolicy, IncidentRequest
from .ingest import HeartbeatMonitor, TelemetryIngestor, now_ms
from .listener import ChainEventListener
from .send_cmd import make_command
from .settings import hmac_key, open_store, sqlite_path
from .storage import LogStore

log = logging.getLogger("hrc-backend")
PRIO_INCIDENT, PRIO_BATCH = 0, 1


class BackendService:
    def __init__(self, store: LogStore, key: bytes, detector: HrcDetector,
                 chain_factory: Callable[[], ChainClient], mqtt_cfg: MqttSettings | None = None,
                 batch_interval_ms: int | None = None, clock: Callable[[], int] = now_ms,
                 hub: EventHub | None = None):
        self.cfg = load_config()
        self.store, self.key, self.detector, self.chain_factory, self.clock = store, key, detector, chain_factory, clock
        self.hub = hub or EventHub()
        self.ssm = ssm_params()
        self.last_tel: dict[int, dict] = {}  # bản tin telemetry (kèm AI) mới nhất – cho /api/state
        self.current_operator: dict[int, str] = {}
        self.ingest = TelemetryIngestor(store, key, clock)
        self.heartbeat = HeartbeatMonitor(store, key, clock)
        self.policy = IncidentPolicy()
        self.batcher = MerkleBatcher(store, clock, max_interval_ms=batch_interval_ms)
        self.evidence: dict[int, deque[Telemetry]] = {}
        self.stats: Counter[str] = Counter()
        self.jobs: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._batch_pending = threading.Event()
        self._stop = threading.Event()
        self.ready = threading.Event()  # chain-writer đã khôi phục + đồng bộ trạng thái
        self.mqtt_connected = threading.Event()
        # Trạng thái khóa theo CHUỖI (nguồn sự thật) – dùng để đối chiếu với heartbeat thiết bị
        self.chain_state: dict[int, int] = {}
        self._last_cmd_at: dict[int, float] = {}
        self._threads: list[threading.Thread] = []

        s = mqtt_cfg or mqtt_settings()
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hrc-backend")
        if s.username:
            self.mqtt.username_pw_set(s.username, s.password)
        self.mqtt.on_connect, self.mqtt.on_message = self._on_connect, self._on_message
        self.mqtt.on_disconnect = lambda *a: self.mqtt_connected.clear()
        self._mqtt_host, self._mqtt_port = s.host, s.port

    # =============================== MQTT ===============================
    def _on_connect(self, c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Broker từ chối kết nối: %s", reason_code)
            return
        c.subscribe([("hrc/+/telemetry", self.cfg["mqtt"]["qos_telemetry"]),
                     ("hrc/+/heartbeat", self.cfg["mqtt"]["qos_heartbeat"])])
        self.mqtt_connected.set()
        log.info("Đã kết nối broker %s:%s", self._mqtt_host, self._mqtt_port)

    def _on_message(self, c, userdata, msg):
        try:
            if msg.topic.endswith("/heartbeat"):
                r = self.heartbeat.ingest(msg.topic, msg.payload)
                self.stats["hb_ok" if r.accepted else f"hb_{r.reason}"] += 1
                if r.accepted:
                    rid = int(msg.topic.split("/")[1])
                    self._reconcile(rid)
                    self.hub.publish({"type": "device", **self._device(rid)})
                return
            r = self.ingest.ingest(msg.topic, msg.payload)
            self.stats["ok" if r.accepted else r.reason] += 1
            if r.telemetry:
                self._after_telemetry(r.telemetry)
        except Exception:
            log.exception("Lỗi xử lý bản tin %s", msg.topic)

    def _after_telemetry(self, t: Telemetry) -> None:
        ev = self.evidence.setdefault(t.robot_id, deque(maxlen=WINDOW))
        ev.append(t)
        pred = self.detector.update(t)
        s_mm = protective_distance_mm(t.v_robot_mmps, self.ssm)
        msg = {"type": "telemetry", "robot_id": t.robot_id, "seq": t.seq, "ts_ms": t.ts_ms,
               "distance_mm": t.distance_mm, "v_robot_mmps": t.v_robot_mmps, "zone": t.zone, "estop": t.estop,
               "s_mm": s_mm, "warn_mm": s_mm + self.ssm.warning_margin_mm, "ai": None}
        if pred is not None:
            self.stats[f"ai_{pred.label}"] += 1
            msg["ai"] = {"label": pred.label, "confidence": round(pred.confidence, 3), "anomaly": pred.anomaly,
                         "if_anomaly": pred.if_anomaly, "ae_anomaly": pred.ae_anomaly,
                         "danger_confirmed": pred.danger_confirmed}
        self.last_tel[t.robot_id] = msg
        self.hub.publish(msg)
        if pred is None:
            return
        req = self.policy.observe(pred, list(ev))
        if req is not None:
            local_id = self.store.create_incident(req.robot_id, req.kind, req.severity, "", "0x" + req.evidence_hash.hex(),
                                                  req.from_seq, req.to_seq, req.confidence, self.clock())
            self.hub.publish({"type": "incident", "status": "PENDING", "robot_id": req.robot_id, "kind": req.kind,
                              "severity": req.severity, "confidence": req.confidence,
                              "from_seq": req.from_seq, "to_seq": req.to_seq})
            self.jobs.put((PRIO_INCIDENT, next(self._seq), ("INCIDENT", (local_id, req))))

    def _device(self, robot_id: int) -> dict:
        st = self.heartbeat.status.get(robot_id)
        return {"robot_id": robot_id, "online": bool(st and st.online),
                "state": st.state if st else None, "estop": st.estop if st else None,
                "cfg_match": st.cfg_match if st else None, "offline_reason": st.offline_reason if st else None}

    def status(self) -> dict:
        """Ảnh chụp trạng thái cho /api/state và bản tin snapshot của WebSocket."""
        robots = []
        for rid in self.batcher.robot_ids:
            robots.append({
                "robot_id": rid,
                "device": self._device(rid),
                "chain_state": self.chain_state.get(rid),  # 0 ACTIVE, 1 LOCKED (theo event on-chain)
                "current_operator": self.current_operator.get(rid),
                "last": self.last_tel.get(rid),
            })
        return {"robots": robots, "stats": dict(self.stats), "mqtt_connected": self.mqtt_connected.is_set(),
                "chain_ready": self.ready.is_set()}

    def send_command(self, robot_id: int, cmd: str) -> None:
        c = make_command(self.store, self.key, robot_id, cmd)
        self.mqtt.publish(topic("cmd", robot_id), c.to_json(), qos=self.cfg["mqtt"]["qos_cmd"])
        self._last_cmd_at[robot_id] = time.monotonic()
        self.hub.publish({"type": "command", "robot_id": robot_id, "cmd": cmd, "nonce": c.nonce})
        log.warning(">>> Gửi %s tới robot %d (nonce %d)", cmd, robot_id, c.nonce)

    RECONCILE_MIN_INTERVAL_S = 3.0

    def _reconcile(self, robot_id: int) -> None:
        """Heartbeat (có ký) báo trạng thái khóa khác chuỗi => gửi lại lệnh đúng.

        Xử lý trường hợp lệnh bị mất khi thiết bị offline (QoS 1 nhưng không có phiên lưu trữ).
        """
        st = self.heartbeat.status.get(robot_id)
        want = self.chain_state.get(robot_id)
        if st is None or not st.online or want is None or st.state == want:
            return
        if time.monotonic() - self._last_cmd_at.get(robot_id, 0.0) < self.RECONCILE_MIN_INTERVAL_S:
            return
        log.warning("Robot %d lệch trạng thái (thiết bị %s, chuỗi %s) – gửi lại lệnh", robot_id,
                    "LOCKED" if st.state else "ACTIVE", "LOCKED" if want else "ACTIVE")
        self.send_command(robot_id, CMD_LOCK if want == STATE_LOCKED else CMD_UNLOCK)

    # =============================== chain-writer ===============================
    def request_batch(self, force: bool = False, done: threading.Event | None = None) -> None:
        if force or not self._batch_pending.is_set():
            self._batch_pending.set()
            self.jobs.put((PRIO_BATCH, next(self._seq), ("BATCH", (force, done))))

    def _writer(self) -> None:
        chain = self.chain_factory()
        self.batcher.recover(chain)
        for rid in self.batcher.robot_ids:  # đồng bộ trạng thái khóa hiện tại từ chuỗi
            info = chain.robot_info(rid)
            self.chain_state[rid] = info.state
            self.current_operator[rid] = info.current_operator
            if self.chain_state[rid] == STATE_LOCKED:
                self.policy.on_locked(rid)
                self.mqtt_connected.wait(10)
                self.send_command(rid, CMD_LOCK)
        self.ready.set()
        while not self._stop.is_set():
            try:
                _, _, (kind, payload) = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "INCIDENT":
                    self._do_incident(chain, *payload)
                elif kind == "BATCH":
                    force, done = payload
                    self._batch_pending.clear()
                    for b in self.batcher.run_once(chain, force=force):
                        self.stats["batches"] += 1
                        self.hub.publish({"type": "batch", **asdict(b)})
                    if done:
                        done.set()
            except Exception:
                log.exception("Lỗi xử lý job %s", kind)

    def _do_incident(self, chain: ChainClient, local_id: int, req: IncidentRequest) -> None:
        operator = chain.robot_info(req.robot_id).current_operator if req.blame_operator else ZERO_ADDRESS
        try:
            tx, incident_id = chain.report_incident(req.robot_id, req.evidence_hash, req.severity, operator)
        except ChainError as e:
            self.store.update_incident(local_id, "FAILED", error=str(e))
            self.policy.rearm(req.robot_id)
            self.hub.publish({"type": "incident", "status": "FAILED", "robot_id": req.robot_id, "kind": req.kind,
                              "error": str(e)})
            log.error("reportIncident thất bại: %s", e)
            return
        self.store.update_incident(local_id, "SENT", incident_id, tx.tx_hash, operator=operator)
        self.hub.publish({"type": "incident", "status": "SENT", "robot_id": req.robot_id, "kind": req.kind,
                          "severity": req.severity, "incident_id": incident_id, "operator": operator,
                          "tx_hash": tx.tx_hash, "gas_used": tx.gas_used})
        self.stats["incidents"] += 1
        log.warning("GHI SỰ CỐ on-chain #%d robot %d (%s, mức %d, operator %s) tx=%s gas=%d",
                    incident_id, req.robot_id, req.kind, req.severity, operator[:10], tx.tx_hash[:12], tx.gas_used)

    # =============================== chain-listener ===============================
    def _on_chain_event(self, ev: dict) -> None:
        name, args = ev["event"], ev["args"]
        self.hub.publish({"type": "chain_event", "event": name, "block": ev["block"], "tx_hash": ev["tx_hash"],
                          "args": {k: (("0x" + v.hex()) if isinstance(v, bytes) else v) for k, v in args.items()}})
        if name == "OperatorCheckedIn":
            self.current_operator[args["robotId"]] = args["operator"]
        elif name == "OperatorCheckedOut":
            self.current_operator[args["robotId"]] = ZERO_ADDRESS
        if name == "EmergencyStop":
            self.chain_state[args["robotId"]] = STATE_LOCKED
            self.policy.on_locked(args["robotId"])
            self.send_command(args["robotId"], CMD_LOCK)
        elif name == "Unlocked":
            self.chain_state[args["robotId"]] = STATE_ACTIVE
            self.policy.on_unlocked(args["robotId"])
            log.warning("Event Unlocked: robot %d được SUPERVISOR %s mở khóa", args["robotId"], args["supervisor"][:10])
            self.send_command(args["robotId"], CMD_UNLOCK)
        elif name == "OperatorBlocked":
            log.error("Operator %s BỊ KHÓA QUYỀN sau %d vi phạm", args["operator"], args["violations"])

    def _listener(self) -> None:
        chain = self.chain_factory()
        listener = ChainEventListener(chain, self.store, self._on_chain_event)
        poll_s = self.cfg["chain"]["listener_poll_ms"] / 1000
        while not self._stop.wait(poll_s):
            try:
                listener.poll()
            except Exception:
                log.exception("Lỗi đọc event")

    # =============================== vòng đời ===============================
    def start(self) -> None:
        self.mqtt.connect_async(self._mqtt_host, self._mqtt_port, keepalive=self.cfg["mqtt"]["keepalive_s"])
        self.mqtt.loop_start()
        for name, target in (("chain-writer", self._writer), ("chain-listener", self._listener)):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def tick(self) -> None:
        self.heartbeat.check_timeouts()
        self.request_batch()

    def flush(self, timeout: float = 60) -> bool:
        """Neo ngay mọi bản ghi chưa neo và chờ xong (dùng khi dừng dịch vụ / cuối demo)."""
        done = threading.Event()
        self.request_batch(force=True, done=done)
        return done.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self.mqtt.loop_stop()
        self.mqtt.disconnect()


def open_store_for_contract(path: Path, chain_id: int, address: str) -> LogStore:
    """Mỗi DB gắn với ĐÚNG 1 contract. Contract đổi (deploy lại / đổi mạng) => cất DB cũ sang file lưu trữ
    (không xóa) rồi dùng DB mới – tránh batchId/seq của contract cũ làm hỏng việc neo log vào contract mới."""
    ident = f"{chain_id}:{address.lower()}"
    store = open_store(path)
    old = store.kv_get("contract")
    if old is None and store.count_telemetry() > 0:
        old = "không rõ (DB cũ chưa gắn contract)"
    if old is not None and old != ident:
        store.close()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(path) + suffix)
            if f.exists():
                f.rename(f.with_name(f"{path.stem}.{stamp}{path.suffix}{suffix}"))
        log.warning("DB %s thuộc contract khác (%s) – đã cất sang %s.%s%s, dùng DB mới",
                    path.name, old, path.stem, stamp, path.suffix)
        store = open_store(path)
    store.kv_set("contract", ident)
    return store


def main(argv: list[str] | None = None) -> int:
    load_env()
    ap = argparse.ArgumentParser(description="HRC backend đầy đủ (ingest + AI + blockchain + API dashboard)")
    ap.add_argument("--batch-interval-ms", type=int, help="ghi đè [merkle].batch_max_interval_ms (demo)")
    ap.add_argument("--duration", type=float, default=0, help="giây chạy (0 = chạy mãi)")
    ap.add_argument("--stats-every", type=float, default=10.0)
    ap.add_argument("--api-host", default=os.getenv("BACKEND_HOST", "127.0.0.1"))
    ap.add_argument("--api-port", type=int, default=int(os.getenv("BACKEND_PORT", "8000")))
    ap.add_argument("--db", type=Path, help="đường dẫn SQLite (mặc định SQLITE_PATH trong .env)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        key = hmac_key()
        probe = ChainClient.from_env("DEVICE")  # kiểm tra sớm: RPC, contract, khóa
        detector = HrcDetector()
    except (ValueError, ChainError, FileNotFoundError) as e:
        log.error("%s", e)
        return 2
    if not os.getenv("CHAIN_START_BLOCK"):  # dashboard quét event từ block deploy (RPC công khai giới hạn khoảng quét)
        os.environ["CHAIN_START_BLOCK"] = str(probe.deploy_block())
    log.info("Mạng %s (chain %d), contract %s, quét event từ block %s", network(), probe.chain_id, probe.address,
             os.environ["CHAIN_START_BLOCK"])
    store = open_store_for_contract(args.db or sqlite_path(), probe.chain_id, probe.address)
    hub = EventHub()
    svc = BackendService(store, key, detector, lambda: ChainClient.from_env("DEVICE"),
                         batch_interval_ms=args.batch_interval_ms, hub=hub)
    app = create_app(store, hub, key, lambda: ChainClient(), svc.status,
                     {"chain_id": probe.chain_id, "address": probe.address})
    server = uvicorn.Server(uvicorn.Config(app, host=args.api_host, port=args.api_port, log_level="warning"))
    svc.start()

    def ticker() -> None:
        start = next_stats = time.monotonic()
        while not server.should_exit:
            time.sleep(1.0)
            svc.tick()
            if time.monotonic() >= next_stats:
                next_stats += args.stats_every
                log.info("Thống kê: %s", dict(svc.stats))
            if args.duration > 0 and time.monotonic() - start >= args.duration:
                server.should_exit = True

    threading.Thread(target=ticker, name="ticker", daemon=True).start()
    log.info("API dashboard: http://%s:%d  (WebSocket /ws)", args.api_host, args.api_port)
    try:
        server.run()  # chặn tới khi Ctrl+C hoặc hết --duration
    finally:
        log.info("Dừng – neo nốt log còn lại...")
        svc.flush(30)
        svc.stop()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
