"""AI: đặc trưng (online == offline), backend nạp được model, hành vi trên dữ liệu chưa thấy."""
from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from hrc_ai.features import FEATURE_NAMES, N_FEATURES, WINDOW, compute_features
from hrc_ai.inference import MAX_SEQ_GAP, MODEL_DIR, HrcDetector
from hrc_backend.ai import load_detector
from hrc_sim.device import MemoryNvs, SimDevice
from hrc_sim.offline import run_offline
from hrc_sim.scenarios import make_scenario

KEY = bytes(range(32))


def sim(name: str, seed: int, n: int):
    return list(run_offline(make_scenario(name, seed), SimDevice(1, KEY, MemoryNvs()), n))


def cols(steps):
    t = [s.telemetry for s in steps]
    return ([x.distance_mm for x in t], [x.v_robot_mmps for x in t], [x.zone for x in t], [x.estop for x in t])


# ---------------- đặc trưng ----------------
def test_feature_shape_and_names():
    x = compute_features(*cols(sim("mixed", 1, 100)))
    assert x.shape == (100 - WINDOW + 1, N_FEATURES) == (81, len(FEATURE_NAMES))
    assert x.dtype == np.float32 and np.isfinite(x).all()


def test_online_equals_offline():
    """Tính theo lô (huấn luyện) và tính từng cửa sổ (backend) phải ra CÙNG đặc trưng."""
    d, v, z, e = cols(sim("sensor_noise", 3, 200))
    batch = compute_features(d, v, z, e)
    for i in range(0, len(d) - WINDOW + 1, 17):
        one = compute_features(d[i:i + WINDOW], v[i:i + WINDOW], z[i:i + WINDOW], e[i:i + WINDOW])
        np.testing.assert_allclose(one[0], batch[i], rtol=1e-6, atol=1e-4)


def test_too_short_window_rejected():
    with pytest.raises(ValueError):
        compute_features([1] * (WINDOW - 1), [0] * (WINDOW - 1), [0] * (WINDOW - 1), [0] * (WINDOW - 1))


def _feat(d):
    n = len(d)
    return dict(zip(FEATURE_NAMES, compute_features(d, [150] * n, [0] * n, [0] * n)[-1]))


def test_spike_feature():
    f = _feat([600] * (WINDOW - 1) + [40])
    assert f["n_jumps"] >= 1 and f["dd_absmax_mmps"] > 5000 and f["d_last_minus_med"] < -500


def test_dropout_vs_leaving_range():
    """Mất mục tiêu đột ngột khi người ở gần = dropout; đi dần ra ngoài tầm đo thì KHÔNG."""
    dropout = _feat([600] * (WINDOW - 3) + [8190] * 3)
    leaving = _feat(list(range(1000, 1000 + 15 * (WINDOW - 3), 15)) + [8190] * 3)
    assert dropout["n_abrupt_oor"] == 1
    assert leaving["n_abrupt_oor"] == 0


def test_approach_slope_negative():
    f = _feat(list(range(900, 900 - 25 * WINDOW, -25)))  # tiến lại 250 mm/s
    assert f["d_slope_mmps"] == pytest.approx(-250, rel=0.01)


# ---------------- model: backend nạp được ----------------
@pytest.fixture(scope="module")
def det() -> HrcDetector:
    assert (MODEL_DIR / "model_meta.json").exists(), "Chưa có model – chạy: python -m hrc_ai.train"
    return load_detector()


def test_backend_loads_model(det):
    assert set(det.labels) == {"NORMAL", "INTRUSION", "OVERSPEED", "SENSOR_FAULT"}
    assert det.if_threshold > 0 and det.ae_threshold > 0


def test_stream_needs_full_window(det):
    steps = sim("normal", 11, WINDOW + 1)
    out = [det.update(s.telemetry) for s in steps]
    assert all(p is None for p in out[: WINDOW - 1])
    assert out[WINDOW - 1] is not None and out[WINDOW - 1].seq == steps[WINDOW - 1].telemetry.seq


def test_stream_resets_on_big_seq_gap(det):
    steps = sim("normal", 12, 2 * WINDOW + MAX_SEQ_GAP + 5)
    for s in steps[:WINDOW]:
        det.update(s.telemetry)
    assert det.update(steps[WINDOW + MAX_SEQ_GAP + 1].telemetry) is None  # nhảy quá xa => xóa cửa sổ


def test_stream_equals_batch(det):
    """Luồng backend (update từng bản ghi) phải cho ĐÚNG kết quả như chấm theo lô."""
    steps = sim("mixed", 31, 80)
    det._buffers.clear()
    det._last_seq.clear()
    stream = [p for p in (det.update(s.telemetry) for s in steps) if p is not None]
    batch = det.score_sequence([s.telemetry for s in steps])
    assert len(stream) == len(batch["label"]) == 80 - WINDOW + 1
    assert [p.label for p in stream] == list(batch["label"])
    np.testing.assert_allclose([p.ae_error for p in stream], batch["ae_error"], rtol=1e-5)
    np.testing.assert_allclose([p.if_score for p in stream], batch["if_score"], rtol=1e-6)


def _batch(det, name, seed, n):
    """Chấm theo lô (nhanh) – trả về (mẫu thế giới, telemetry, kết quả) cho mỗi cửa sổ."""
    steps = sim(name, seed, n)
    s = det.score_sequence([st.telemetry for st in steps])
    tail = steps[WINDOW - 1:]
    return tail, s


def test_real_intrusion_confirmed(det):
    """Người thật vào vùng DANGER => AI xác nhận INTRUSION (seed chưa dùng khi huấn luyện)."""
    tail, s = _batch(det, "approach", 777, 1500)
    mask = np.array([st.telemetry.zone == 2 and st.sample.label == "INTRUSION" for st in tail])
    assert mask.sum() > 50
    assert np.mean(s["label"][mask] == "INTRUSION") >= 0.9


def test_sensor_spike_not_confirmed_as_intrusion(det):
    """Xung nhiễu làm zone = DANGER nhưng AI KHÔNG được xác nhận là người => tránh sự cố giả on-chain."""
    tail, s = _batch(det, "sensor_noise", 777, 4000)
    mask = np.array([st.telemetry.zone == 2 and st.sample.fault == "spike" for st in tail])
    assert mask.sum() > 10
    assert np.mean(s["label"][mask] == "INTRUSION") <= 0.1


def test_normal_mostly_quiet(det):
    tail, s = _batch(det, "normal", 778, 3000)
    k = slice(WINDOW, None)  # bỏ giai đoạn khởi động (relay ngắt lúc boot)
    assert np.mean(s["label"][k] == "NORMAL") >= 0.97
    assert np.mean((s["if_anomaly"] | s["ae_anomaly"])[k]) <= 0.05


@pytest.mark.skipif(importlib.util.find_spec("keras") is None, reason="không có keras (chỉ cần khi huấn luyện)")
def test_numpy_autoencoder_matches_keras(det):
    import keras

    model = keras.models.load_model(MODEL_DIR / "autoencoder.keras")
    x = det.scaler.transform(compute_features(*cols(sim("mixed", 5, 300)))).astype(np.float32)
    ref = model.predict(x, verbose=0)
    # float32 + oneDNN cộng dồn khác thứ tự => sai số làm tròn tỉ lệ với độ lớn (đo được ~5e-7 tương đối)
    np.testing.assert_allclose(det.ae.reconstruct(x), ref, rtol=1e-5, atol=1e-3)
    ref_err = np.mean((ref - x) ** 2, axis=1)
    np.testing.assert_allclose(det.ae.error(x), ref_err, rtol=1e-4, atol=1e-6)  # đại lượng dùng để quyết định
