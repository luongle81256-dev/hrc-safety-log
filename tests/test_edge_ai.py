"""Edge AI INT8 – kiểm chứng TOÀN chuỗi, mỗi mắt xích 1 test:

    TFLite interpreter (kernel tham chiếu)  ==  runtime INT8 numpy  ==  runtime INT8 C++ của firmware   (TRÙNG TỪNG BIT)
    features.py (numpy)                     ≈   hrc_features.cpp                                       (sai số làm tròn)
    quyết định bất thường Python            ==  quyết định trên firmware C++                           (end-to-end)
"""
from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from hrc_ai.edge import EDGE_META, HEADER, INT8_TFLITE, Int8Model
from hrc_ai.features import WINDOW, compute_features, feature_fingerprint
from hrc_ai.inference import HrcDetector
from hrc_sim.device import MemoryNvs, SimDevice
from hrc_sim.offline import run_offline
from hrc_sim.scenarios import SCENARIOS, make_scenario

HAS_TF = importlib.util.find_spec("tensorflow") is not None
KEY = bytes(range(32))


@pytest.fixture(scope="module")
def model() -> Int8Model:
    return Int8Model.load()


@pytest.fixture(scope="module")
def det() -> HrcDetector:
    return HrcDetector()


def _sequences(n_per: int = 400):
    """Telemetry mô phỏng của mọi kịch bản (seed chưa dùng khi huấn luyện)."""
    for name in SCENARIOS:
        tels = [s.telemetry for s in run_offline(make_scenario(name, 9001), SimDevice(1, KEY, MemoryNvs()), n_per)]
        yield name, tels


def _cols(tels):
    return ([t.distance_mm for t in tels], [t.v_robot_mmps for t in tels], [t.zone for t in tels],
            [t.estop for t in tels])


# ---------------- model & header ----------------
def test_int8_model_properties(model):
    assert INT8_TFLITE.stat().st_size < 8_000
    assert len(model.layers) == 6 and all(L.w.dtype == np.int8 for L in model.layers)
    assert all(len(L.mult) == L.w.shape[0] for L in model.layers)  # lượng tử theo kênh


def test_header_matches_current_features():
    """Header firmware sinh cho ĐÚNG bộ đặc trưng/config hiện tại (chống quên chạy lại hrc_ai.edge)."""
    text = HEADER.read_text(encoding="utf-8")
    assert f'HRC_EDGE_FEATURE_FINGERPRINT "{feature_fingerprint()}"' in text


# ---------------- runtime INT8: TFLite == numpy == C++ ----------------
@pytest.mark.skipif(not HAS_TF, reason="cần TensorFlow để chạy TFLite interpreter")
def test_numpy_runtime_bit_exact_vs_tflite(model, det):
    from hrc_ai.edge import run_tflite

    rng = np.random.default_rng(0)
    real = det.scaler.transform(np.vstack([compute_features(*_cols(t)) for _, t in _sequences(150)])).astype(np.float32)
    q = np.vstack([model.quantize_input(real),
                   rng.integers(-128, 128, size=(500, 20), dtype=np.int8),  # cả đầu vào ngẫu nhiên/bão hòa
                   np.full((1, 20), 127, np.int8), np.full((1, 20), -128, np.int8)])
    assert np.array_equal(model.run(q), run_tflite(INT8_TFLITE, q))


@pytest.mark.gcc
def test_cpp_runtime_bit_exact_vs_numpy(fw_host, model):
    rng = np.random.default_rng(1)
    q = np.vstack([rng.integers(-128, 128, size=(300, 20), dtype=np.int8),
                   np.full((1, 20), 127, np.int8), np.full((1, 20), -128, np.int8)])
    expected = model.run(q)
    for row, exp in zip(q, expected):
        got = fw_host("NN " + " ".join(map(str, row.tolist())))
        assert got == " ".join(map(str, exp.tolist()))


@pytest.mark.gcc
def test_cpp_self_test_vectors(fw_host):
    assert fw_host("EDGEGOLD") == "OK"


# ---------------- đặc trưng + quyết định end-to-end ----------------
@pytest.mark.gcc
def test_cpp_features_match_python(fw_host):
    worst = 0.0
    for _, tels in _sequences(200):
        d, v, z, e = _cols(tels)
        py = compute_features(d, v, z, e)  # float32
        for i in range(0, len(tels) - WINDOW + 1, 7):
            args = [*d[i:i + WINDOW], *v[i:i + WINDOW], *z[i:i + WINDOW], *e[i:i + WINDOW]]
            cpp = np.array(fw_host("FEAT " + " ".join(map(str, args))).split(), dtype=np.float64)
            # C++ tính bằng double; Python ép về float32 => sai lệch chỉ ở mức làm tròn float32
            np.testing.assert_allclose(cpp.astype(np.float32), py[i], rtol=1e-6, atol=1e-4)
            worst = max(worst, float(np.max(np.abs(cpp - py[i].astype(np.float64)))))
    assert worst < 1e-2


@pytest.mark.gcc
def test_edge_decisions_match_python(fw_host, model, det):
    """Firmware (đặc trưng C++ + chuẩn hóa + INT8) ra CÙNG quyết định bất thường với Python trên mọi kịch bản."""
    thr = float(__import__("json").loads(EDGE_META.read_text(encoding="utf-8"))["threshold_int8"])
    total = same = 0
    anomalies = 0
    for _, tels in _sequences(400):
        feats = compute_features(*_cols(tels))
        py_err = model.error(det.scaler.transform(feats).astype(np.float32))
        for f, pe in zip(feats[::3], py_err[::3]):
            err, flag = fw_host("EDGE " + " ".join(f"{x:.9g}" for x in f.astype(np.float64))).split()
            assert abs(float(err) - pe) <= 1e-4 * max(1.0, pe), (float(err), pe)
            total += 1
            same += int(flag) == int(pe > thr)
            anomalies += int(flag)
    assert same == total  # trùng 100% quyết định
    assert 0 < anomalies < total  # có cả 2 loại để phép so có ý nghĩa
