"""Chạy simulator KHÔNG cần MQTT, đồng hồ ảo – dùng cho test và sinh dataset (Phase 3)."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from hrc_common.protocol import Telemetry

from .device import SimDevice
from .scenarios import Scenario, WorldSample

# Mốc thời gian ảo cố định để dataset tái lập được: 2026-09-21 00:00:00 UTC
VIRTUAL_EPOCH_MS = 1_790_035_200_000


@dataclass(frozen=True, slots=True)
class Step:
    sample: WorldSample
    telemetry: Telemetry


def run_offline(scenario: Scenario, device: SimDevice, n_ticks: int,
                start_ms: int = VIRTUAL_EPOCH_MS) -> Iterator[Step]:
    """Mỗi chu kỳ: thế giới sinh mẫu -> thiết bị xử lý -> telemetry đã ký."""
    for i in range(n_ticks):
        s = scenario.step()
        t = device.tick(start_ms + i * scenario.period_ms, s.measured_distance_mm, s.v_cmd_mmps)
        yield Step(s, t)
