"""Chính sách chuyển kết quả AI -> yêu cầu reportIncident.

- INTRUSION: zone DANGER + AI xác nhận người thật (Prediction.danger_confirmed). Quy lỗi operator đang check-in.
  Nếu relay KHÔNG ngắt (estop=0) mà vẫn DANGER => fast lane hỏng => mức nghiêm trọng cao nhất.
- OVERSPEED: robot vượt tốc kéo dài. Lỗi máy => KHÔNG quy lỗi người (operator = 0).
- SENSOR_FAULT: lỗi cảm biến kéo dài (xung lẻ chỉ ghi log). Không quy lỗi người.
Chống báo lặp: sau khi phát 1 yêu cầu, robot "tắt chuông" cho tới khi nhận event Unlocked từ chuỗi.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from hrc_ai.inference import Prediction
from hrc_common.config import load_config
from hrc_common.merkle import merkle_root, telemetry_leaf
from hrc_common.protocol import Telemetry

log = logging.getLogger("hrc-incident")

KIND_INTRUSION = "INTRUSION"
KIND_OVERSPEED = "OVERSPEED"
KIND_SENSOR_FAULT = "SENSOR_FAULT"


@dataclass(frozen=True, slots=True)
class IncidentRequest:
    robot_id: int
    kind: str
    severity: int
    blame_operator: bool  # True: quy lỗi operator đang check-in (đọc từ chuỗi lúc gửi)
    evidence_hash: bytes  # Merkle root của các bản ghi trong cửa sổ bằng chứng (cùng định dạng leaf)
    from_seq: int
    to_seq: int
    confidence: float


class IncidentPolicy:
    def __init__(self):
        c = load_config()["incident"]
        self.min_consecutive = {
            KIND_INTRUSION: c["intrusion_min_consecutive"],
            KIND_OVERSPEED: c["overspeed_min_consecutive"],
            KIND_SENSOR_FAULT: c["sensor_fault_min_consecutive"],
        }
        self.sev = c
        self._streak: dict[tuple[int, str], int] = {}
        self._muted: set[int] = set()  # robot đã có sự cố, chờ Unlocked

    # ---- trạng thái từ chuỗi ----
    def on_locked(self, robot_id: int) -> None:
        self._muted.add(robot_id)

    def on_unlocked(self, robot_id: int) -> None:
        self._muted.discard(robot_id)
        for k in list(self._streak):
            if k[0] == robot_id:
                del self._streak[k]

    def rearm(self, robot_id: int) -> None:
        """Gửi reportIncident thất bại => cho phép báo lại."""
        self._muted.discard(robot_id)

    def is_muted(self, robot_id: int) -> bool:
        return robot_id in self._muted

    # ---- quyết định ----
    def _kind(self, p: Prediction) -> str | None:
        if p.danger_confirmed:
            return KIND_INTRUSION
        if p.label in (KIND_OVERSPEED, KIND_SENSOR_FAULT):
            return p.label
        return None

    def observe(self, p: Prediction, evidence: Sequence[Telemetry]) -> IncidentRequest | None:
        """Gọi cho MỖI dự đoán. `evidence` = cửa sổ bản ghi AI vừa dùng (bản ghi cuối là bản ghi hiện tại)."""
        kind = self._kind(p)
        for k in (KIND_INTRUSION, KIND_OVERSPEED, KIND_SENSOR_FAULT):
            key = (p.robot_id, k)
            self._streak[key] = self._streak.get(key, 0) + 1 if k == kind else 0
        if kind is None or p.robot_id in self._muted or self._streak[(p.robot_id, kind)] < self.min_consecutive[kind]:
            return None

        current = evidence[-1]
        if kind == KIND_INTRUSION:
            severity = self.sev["severity_intrusion_no_estop"] if current.estop == 0 else self.sev["severity_intrusion"]
        elif kind == KIND_OVERSPEED:
            severity = self.sev["severity_overspeed"]
        else:
            severity = self.sev["severity_sensor_fault"]
        self._muted.add(p.robot_id)
        req = IncidentRequest(
            robot_id=p.robot_id, kind=kind, severity=severity, blame_operator=kind == KIND_INTRUSION,
            evidence_hash=merkle_root([telemetry_leaf(t) for t in evidence]),
            from_seq=evidence[0].seq, to_seq=current.seq, confidence=p.confidence,
        )
        log.warning("SỰ CỐ robot %d: %s (độ tin cậy %.2f, mức %d, bằng chứng seq %d..%d)",
                    req.robot_id, kind, p.confidence, severity, req.from_seq, req.to_seq)
        return req
