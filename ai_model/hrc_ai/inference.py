"""Suy luận cho backend: nạp model đã huấn luyện, chấm điểm từng bản ghi telemetry theo thời gian thực.

KHÔNG phụ thuộc TensorFlow/pandas: autoencoder chạy bằng numpy từ trọng số đã xuất (.npz) – nhẹ, nhanh,
và giống cách ESP32 sẽ tính ở Phase 7. Test đảm bảo kết quả numpy == Keras.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import sklearn

from hrc_common.config import ROOT_DIR
from hrc_common.protocol import Telemetry

from .features import FEATURE_NAMES, WINDOW, compute_features, feature_fingerprint

log = logging.getLogger("hrc-ai")

MODEL_DIR = ROOT_DIR / "ai_model" / "models"
BUNDLE_FILE = "hrc_models.joblib"
AE_NPZ_FILE = "autoencoder.npz"
META_FILE = "model_meta.json"
MAX_SEQ_GAP = 3  # mất <= 3 bản tin liên tiếp vẫn giữ cửa sổ; nhiều hơn => xóa cửa sổ, chờ đủ lại

ZONE_DANGER = 2
LABEL_NORMAL = "NORMAL"
LABEL_INTRUSION = "INTRUSION"


class NumpyAutoencoder:
    """Lan truyền thẳng Dense bằng numpy từ trọng số Keras đã xuất."""

    _ACTS = {"relu": lambda x: np.maximum(x, 0.0), "linear": lambda x: x}

    def __init__(self, npz_path: Path):
        data = np.load(npz_path)
        n = int(data["n_layers"])
        acts = [str(a) for a in data["activations"]]
        self.layers = [(data[f"W{i}"], data[f"b{i}"], self._ACTS[acts[i]]) for i in range(n)]

    def reconstruct(self, xs: np.ndarray) -> np.ndarray:
        h = xs.astype(np.float32)
        for w, b, act in self.layers:
            h = act(h @ w + b)
        return h

    def error(self, xs: np.ndarray) -> np.ndarray:
        """MSE tái tạo cho từng hàng."""
        return np.mean((self.reconstruct(xs) - xs) ** 2, axis=1)


@dataclass(frozen=True, slots=True)
class Prediction:
    robot_id: int
    seq: int
    zone: int
    label: str  # nhãn phân loại (RandomForest)
    confidence: float  # xác suất của nhãn đó
    proba: dict[str, float]
    if_score: float  # IsolationForest: càng lớn càng bất thường
    if_anomaly: bool
    ae_error: float  # Autoencoder: lỗi tái tạo
    ae_anomaly: bool

    @property
    def anomaly(self) -> bool:
        """Bất thường nếu BẤT KỲ mô hình nào báo (ưu tiên không bỏ sót)."""
        return self.if_anomaly or self.ae_anomaly or self.label != LABEL_NORMAL

    @property
    def danger_confirmed(self) -> bool:
        """DANGER được AI xác nhận là người thật xâm nhập (điều kiện để reportIncident ở Phase 4)."""
        return self.zone == ZONE_DANGER and self.label == LABEL_INTRUSION


class HrcDetector:
    def __init__(self, model_dir: Path = MODEL_DIR):
        model_dir = Path(model_dir)
        meta_path = model_dir / META_FILE
        if not meta_path.exists():
            raise FileNotFoundError(f"Chưa có model ở {model_dir}. Chạy: python -m hrc_ai.train")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if tuple(self.meta["feature_names"]) != FEATURE_NAMES or self.meta["window"] != WINDOW:
            raise ValueError("Model được huấn luyện với bộ đặc trưng khác code hiện tại – hãy huấn luyện lại")
        if self.meta["sklearn_version"] != sklearn.__version__:
            log.warning("Model lưu bằng scikit-learn %s, đang chạy %s – nên cài đúng phiên bản",
                        self.meta["sklearn_version"], sklearn.__version__)
        if self.meta.get("feature_fingerprint") != feature_fingerprint():
            log.warning("Phần config ảnh hưởng tới đặc trưng (SSM/chu kỳ/fast lane) đã đổi kể từ lúc huấn luyện"
                        " – hãy chạy lại: python -m hrc_ai.dataset --regenerate; python -m hrc_ai.train")

        bundle = joblib.load(model_dir / BUNDLE_FILE)
        self.scaler = bundle["scaler"]
        self.iforest = bundle["iforest"]
        self.classifier = bundle["classifier"]
        # Suy luận từng mẫu: 1 luồng nhanh hơn nhiều (n_jobs=-1 tốn công dựng thread pool mỗi lần gọi)
        self.iforest.n_jobs = 1
        self.classifier.n_jobs = 1
        self.labels: list[str] = [str(c) for c in self.classifier.classes_]
        self.ae = NumpyAutoencoder(model_dir / AE_NPZ_FILE)
        self.if_threshold = float(self.meta["thresholds"]["iforest"])
        self.ae_threshold = float(self.meta["thresholds"]["autoencoder"])
        self._buffers: dict[int, deque] = {}
        self._last_seq: dict[int, int] = {}

    # ---------------- theo lô (huấn luyện / đánh giá) ----------------
    def score(self, x: np.ndarray) -> dict[str, np.ndarray]:
        xs = self.scaler.transform(x).astype(np.float32)
        proba = self.classifier.predict_proba(x)
        if_score = -self.iforest.score_samples(xs)
        ae_error = self.ae.error(xs)
        return {
            "proba": proba,
            "label": np.asarray(self.labels)[proba.argmax(axis=1)],
            "if_score": if_score,
            "if_anomaly": if_score > self.if_threshold,
            "ae_error": ae_error,
            "ae_anomaly": ae_error > self.ae_threshold,
        }

    def score_sequence(self, tels: list[Telemetry]) -> dict[str, np.ndarray]:
        """Chấm điểm theo lô 1 chuỗi telemetry liên tục (nhanh – dùng cho test/đánh giá/phân tích log).

        Kết quả hàng i ứng với cửa sổ kết thúc tại tels[i + WINDOW - 1]; giống hệt update() từng bản ghi.
        """
        x = compute_features([t.distance_mm for t in tels], [t.v_robot_mmps for t in tels],
                             [t.zone for t in tels], [t.estop for t in tels])
        return self.score(x)

    # ---------------- theo luồng (backend) ----------------
    def update(self, t: Telemetry) -> Prediction | None:
        """Đưa 1 bản ghi vào cửa sổ của robot. Trả về Prediction khi cửa sổ đủ WINDOW bản ghi."""
        buf = self._buffers.setdefault(t.robot_id, deque(maxlen=WINDOW))
        last = self._last_seq.get(t.robot_id)
        if last is not None and not 0 < t.seq - last <= MAX_SEQ_GAP + 1:
            buf.clear()  # mất quá nhiều bản tin / seq bất thường => bắt đầu cửa sổ mới
        self._last_seq[t.robot_id] = t.seq
        buf.append((t.distance_mm, t.v_robot_mmps, t.zone, t.estop))
        if len(buf) < WINDOW:
            return None

        arr = np.asarray(buf, dtype=np.float64)
        x = compute_features(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3])
        s = self.score(x)
        proba = {lab: float(p) for lab, p in zip(self.labels, s["proba"][0])}
        label = str(s["label"][0])
        return Prediction(
            robot_id=t.robot_id, seq=t.seq, zone=t.zone, label=label, confidence=proba[label], proba=proba,
            if_score=float(s["if_score"][0]), if_anomaly=bool(s["if_anomaly"][0]),
            ae_error=float(s["ae_error"][0]), ae_anomaly=bool(s["ae_anomaly"][0]),
        )
