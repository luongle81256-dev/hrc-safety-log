"""Cầu nối sự kiện: các luồng nền (MQTT, chain-writer, chain-listener) -> mọi client WebSocket (asyncio).

publish() gọi được từ BẤT KỲ luồng nào. Mỗi client có hàng đợi riêng giới hạn kích thước: client chậm
chỉ bị bỏ bớt bản tin cũ, KHÔNG làm nghẽn backend (an toàn và ingest không bao giờ phải chờ dashboard).
"""
from __future__ import annotations

import asyncio
import threading
from collections import deque

HISTORY_PER_ROBOT = 600  # 60 s telemetry ở 10 Hz – đủ vẽ biểu đồ khi dashboard mới mở
QUEUE_MAX = 1000


class EventHub:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._history: dict[int, deque] = {}
        self.dropped = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def history(self, robot_id: int, limit: int = HISTORY_PER_ROBOT) -> list[dict]:
        with self._lock:
            return list(self._history.get(robot_id, ()))[-limit:]

    def publish(self, msg: dict) -> None:
        """An toàn đa luồng. Telemetry được lưu thêm vào lịch sử ngắn hạn."""
        if msg.get("type") == "telemetry":
            with self._lock:
                self._history.setdefault(msg["robot_id"], deque(maxlen=HISTORY_PER_ROBOT)).append(msg)
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._fanout, msg)

    def _fanout(self, msg: dict) -> None:  # chạy trong event loop
        for q in list(self._queues):
            if q.full():
                q.get_nowait()  # bỏ bản tin cũ nhất của client chậm
                self.dropped += 1
            q.put_nowait(msg)
