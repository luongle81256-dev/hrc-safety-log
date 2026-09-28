"""Nghe event on-chain (polling get_logs) – chuỗi là NGUỒN SỰ THẬT về trạng thái khóa.

EmergencyStop -> backend gửi LOCK;  Unlocked (do SUPERVISOR ký, kể cả từ dashboard/MetaMask) -> gửi UNLOCK.
Vị trí block đã xử lý được lưu SQLite. Lần đầu chạy: bỏ qua lịch sử cũ (không phát lại lệnh cũ) và
đồng bộ trạng thái hiện tại bằng robot_info() (reconcile).
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from .chain import ChainClient
from .storage import LogStore

log = logging.getLogger("hrc-listener")
MAX_RANGE = 2000  # số block tối đa mỗi lần get_logs (RPC công cộng thường giới hạn)


class ChainEventListener:
    def __init__(self, chain: ChainClient, store: LogStore, on_event: Callable[[dict], None]):
        self.chain = chain
        self.store = store
        self.on_event = on_event
        self.kv_key = f"listener:last_block:{chain.chain_id}:{chain.address}"

    def start_position(self) -> int:
        """Block cuối đã xử lý. Chưa có => bắt đầu từ block hiện tại (không phát lại lịch sử)."""
        v = self.store.kv_get(self.kv_key)
        if v is None:
            head = self.chain.block_number()
            self.store.kv_set(self.kv_key, str(head))
            log.info("Listener bắt đầu từ block %d (bỏ qua lịch sử)", head)
            return head
        return int(v)

    def poll(self) -> int:
        """Đọc event mới, gọi on_event theo đúng thứ tự. Trả về số event đã xử lý."""
        last = self.start_position()
        head = self.chain.block_number()
        n = 0
        while last < head:
            to = min(head, last + MAX_RANGE)
            for ev in self.chain.get_events(last + 1, to):
                self.on_event(ev)
                n += 1
            last = to
            self.store.kv_set(self.kv_key, str(last))  # chỉ lưu sau khi xử lý xong cả đoạn
        return n
