"""API cho dashboard: REST + WebSocket real-time (FastAPI).

REST
    GET  /api/health
    GET  /api/config                      chainId, địa chỉ contract, ABI, RPC đọc, explorer, robot, vai trò
    GET  /api/state                       trạng thái hiện tại của từng robot (thiết bị, chuỗi, AI, thống kê)
    GET  /api/telemetry/{robot_id}        lịch sử ngắn (vẽ biểu đồ khi mới mở)
    GET  /api/batches                     các batch Merkle đã neo
    GET  /api/incidents                   sự cố theo góc nhìn backend (kể cả FAILED)
    POST /api/verify/{robot_id}/{batch}   đối soát log off-chain với Merkle root on-chain
WS  /ws                                    {"type": "snapshot"} rồi luồng telemetry/device/incident/batch/chain_event/command

Lịch sử sự cố "chính thức" dashboard tự đọc THẲNG từ event on-chain (không tin backend).
Giới hạn: API không xác thực – chỉ nghe 127.0.0.1 cho demo local (xem README).
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from hrc_common.config import load_config, ssm_params

from .chain import ChainClient, ChainError, load_abi, rpc_url
from .hub import EventHub
from .storage import LogStore
from .verify import verify_batch

EXPLORERS = {1: "https://etherscan.io", 11155111: "https://sepolia.etherscan.io"}


def create_app(store: LogStore, hub: EventHub, key: bytes, chain_factory: Callable[[], ChainClient],
               state_provider: Callable[[], dict], chain_meta: dict) -> FastAPI:
    """chain_meta: {"chain_id", "address"} – đọc 1 lần lúc khởi động."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="HRC Safety Log API", lifespan=lifespan)
    origins = os.getenv("DASHBOARD_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"])
    cfg = load_config()
    p = ssm_params()

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "ws_dropped": hub.dropped}

    @app.get("/api/config")
    def config() -> dict:
        return {
            "chain_id": chain_meta["chain_id"],
            "contract_address": chain_meta["address"],
            # RPC để dashboard ĐỌC chuỗi (không cần ví). Sepolia: đặt DASHBOARD_RPC_URL (RPC công khai).
            "rpc_url": os.getenv("DASHBOARD_RPC_URL") or rpc_url(),
            "explorer": EXPLORERS.get(chain_meta["chain_id"]),
            # Block bắt đầu quét event (Sepolia: đặt = block deploy, RPC công khai giới hạn khoảng quét)
            "start_block": int(os.getenv("CHAIN_START_BLOCK", "0")),
            "abi": load_abi(),
            "robot_ids": cfg["system"]["robot_ids"],
            "zones": cfg["zones"],
            "ssm": {**asdict(p), "profile": cfg["ssm"]["active_profile"]},
            "max_violations": cfg["chain"]["max_violations"],
        }

    @app.get("/api/state")
    def state() -> dict:
        return state_provider()

    @app.get("/api/telemetry/{robot_id}")
    def telemetry(robot_id: int, limit: int = 300) -> list[dict]:
        return hub.history(robot_id, min(limit, 600))

    @app.get("/api/batches")
    def batches(robot_id: int | None = None, limit: int = 50) -> list[dict]:
        rows = store.batches(robot_id)
        return [asdict(b) for b in rows[-limit:]][::-1]

    @app.get("/api/incidents")
    def incidents(robot_id: int | None = None) -> list[dict]:
        return [asdict(i) for i in store.incidents(robot_id)][::-1]

    @app.post("/api/verify/{robot_id}/{batch_id}")
    def verify(robot_id: int, batch_id: int) -> dict:
        try:
            v = verify_batch(store, chain_factory(), key, robot_id, batch_id)
        except ChainError as e:
            raise HTTPException(503, str(e)) from e
        if v.n_records == 0:
            raise HTTPException(404, f"Không có batch {robot_id}/{batch_id} trong log off-chain")
        return {**asdict(v), "root_match": v.root_match, "ok": v.ok}

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        q = hub.subscribe()
        try:
            await sock.send_json({"type": "snapshot", "state": state_provider(),
                                  "history": {rid: hub.history(rid, 300) for rid in cfg["system"]["robot_ids"]}})
            while True:
                await sock.send_json(await q.get())
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(q)

    return app
