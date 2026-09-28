"""Edge AI: lượng tử hóa INT8 autoencoder cho ESP32 + runtime INT8 tham chiếu + sinh header C cho firmware.

Luồng:
    autoencoder.keras --(TFLite converter, full-integer, dữ liệu đại diện NORMAL)--> autoencoder_int8.tflite
    -> trích trọng số int8, bias int32, tham số lượng tử (per-channel) TỪ CHÍNH FILE .tflite
    -> runtime INT8 (numpy, file này) == TFLite interpreter kernel THAM CHIẾU (BUILTIN_REF), TRÙNG TỪNG BIT
    -> header C (iot_code/firmware/include/hrc_edge_model.h) cho runtime C++ trên ESP32 (cũng trùng từng bit)

Vì sao không dùng thư viện TFLite Micro: esp-tflite-micro chính thức là component ESP-IDF (không có cho
PlatformIO/Arduino); bản đóng gói Arduino (TensorFlowLite_ESP32 1.0.0) từ 2022, cũ hơn converter TF 2.21.
Model chỉ có 6 lớp FULLY_CONNECTED (960 phép nhân-cộng) => runtime tự viết theo ĐÚNG đặc tả lượng tử
TFLite (quantization_util.cc, reference integer_ops FullyConnectedPerChannel), kiểm chứng được không cần board.

Chạy:  python -m hrc_ai.edge          (cần TensorFlow: pip install -e ".[ai]")
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from hrc_common.config import ROOT_DIR

from .features import FEATURE_NAMES, JUMP_MMPS, N_FEATURES, TOF_OUT_OF_RANGE_MM, WINDOW, feature_fingerprint
from .inference import MODEL_DIR

F32_TFLITE = MODEL_DIR / "autoencoder_f32.tflite"
INT8_TFLITE = MODEL_DIR / "autoencoder_int8.tflite"
EDGE_NPZ = MODEL_DIR / "edge_int8.npz"
EDGE_META = MODEL_DIR / "edge_meta.json"
HEADER = ROOT_DIR / "iot_code" / "firmware" / "include" / "hrc_edge_model.h"
THRESHOLD_QUANTILE = 0.99
N_GOLDEN = 4  # số vector mẫu nhúng vào firmware để tự kiểm tra lúc khởi động
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


# =============================================================================
# Số học lượng tử – tái hiện ĐÚNG TFLite (tensorflow/lite/kernels/internal)
# =============================================================================

def quantize_multiplier(m: float) -> tuple[int, int]:
    """quantization_util.cc::QuantizeMultiplier – số thực dương -> (multiplier Q31, shift)."""
    if m == 0.0:
        return 0, 0
    q, shift = math.frexp(m)  # m = q * 2^shift, q ∈ [0.5, 1)
    q_fixed = int(math.copysign(math.floor(abs(q) * (1 << 31) + 0.5), q))  # TfLiteRound = làm tròn xa 0
    if q_fixed == (1 << 31):
        q_fixed //= 2
        shift += 1
    if shift < -31:
        return 0, 0
    return q_fixed, shift


def _trunc_div(a: np.ndarray, d: int) -> np.ndarray:
    """Chia nguyên cắt về 0 như C++ (numpy // làm tròn xuống)."""
    return np.where(a >= 0, a // d, -((-a) // d))


def srdhm(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """gemmlowp::SaturatingRoundingDoublingHighMul (int32 x int32 -> int32)."""
    a64, b64 = a.astype(np.int64), b.astype(np.int64)
    ab = a64 * b64
    nudge = np.where(ab >= 0, 1 << 30, 1 - (1 << 30))
    out = _trunc_div(ab + nudge, 1 << 31)
    overflow = (a64 == b64) & (a64 == INT32_MIN)
    return np.where(overflow, INT32_MAX, out)


def rounding_divide_by_pot(x: np.ndarray, exponent: np.ndarray) -> np.ndarray:
    """gemmlowp::RoundingDivideByPOT – chia cho 2^exponent, làm tròn nửa xa 0."""
    x = x.astype(np.int64)
    exponent = exponent.astype(np.int64)
    mask = (np.int64(1) << exponent) - 1
    remainder = x & mask
    threshold = (mask >> 1) + (x < 0)
    return (x >> exponent) + (remainder > threshold)


def mbqm_double(x: np.ndarray, qm: np.ndarray, shift: np.ndarray) -> np.ndarray:
    """MultiplyByQuantizedMultiplier – biến thể làm tròn KÉP (mặc định của TFLite)."""
    left = np.where(shift > 0, shift, 0)
    right = np.where(shift > 0, 0, -shift)
    xl = (x.astype(np.int64) << left).astype(np.int64)
    xl = ((xl + 2**31) % 2**32) - 2**31  # x * (1 << left_shift) trong int32 (tràn = quấn như C)
    return rounding_divide_by_pot(srdhm(xl, qm), right)


def mbqm_single(x: np.ndarray, qm: np.ndarray, shift: np.ndarray) -> np.ndarray:
    """MultiplyByQuantizedMultiplier – biến thể làm tròn ĐƠN (khi build với TFLITE_SINGLE_ROUNDING)."""
    total = (31 - shift).astype(np.int64)
    rnd = np.int64(1) << (total - 1)
    return (x.astype(np.int64) * qm.astype(np.int64) + rnd) >> total


def round_half_away(v: np.ndarray) -> np.ndarray:
    return np.where(v >= 0, np.floor(v + 0.5), np.ceil(v - 0.5))


# =============================================================================
# Model INT8
# =============================================================================

@dataclass
class QLayer:
    w: np.ndarray  # int8 [out, in]
    b: np.ndarray  # int32 [out]
    mult: np.ndarray  # int32 [out]  (per-channel)
    shift: np.ndarray  # int32 [out]
    zp_in: int
    zp_out: int
    act_min: int
    act_max: int


@dataclass
class Int8Model:
    in_scale: float
    in_zp: int
    out_scale: float
    out_zp: int
    layers: list[QLayer]
    double_rounding: bool = True

    def quantize_input(self, xs: np.ndarray) -> np.ndarray:
        """float32 đã chuẩn hóa -> int8 (x/scale tính bằng float32 như C++; làm tròn xa 0; bão hòa)."""
        v = xs.astype(np.float32) / np.float32(self.in_scale)
        return np.clip(round_half_away(v.astype(np.float64)) + self.in_zp, -128, 127).astype(np.int8)

    def run(self, q: np.ndarray) -> np.ndarray:
        """int8 [n, 20] -> int8 [n, 20], đúng reference integer_ops::FullyConnectedPerChannel."""
        mbqm = mbqm_double if self.double_rounding else mbqm_single
        h = q.astype(np.int64)
        for L in self.layers:
            acc = (h - L.zp_in) @ L.w.astype(np.int64).T + L.b.astype(np.int64)
            acc = mbqm(acc, L.mult[None, :], L.shift[None, :]) + L.zp_out
            h = np.clip(acc, L.act_min, L.act_max)
        return h.astype(np.int8)

    def error(self, xs: np.ndarray) -> np.ndarray:
        """MSE giữa đầu ra (giải lượng tử) và đầu vào CHUẨN HÓA GỐC (float) – bất thường vượt dải int8 vẫn cho lỗi lớn."""
        out = (self.run(self.quantize_input(xs)).astype(np.float32) - self.out_zp) * np.float32(self.out_scale)
        return np.mean((out - xs.astype(np.float32)) ** 2, axis=1)

    def save(self, path: Path) -> None:
        arrays = {"meta": np.array([self.in_scale, self.in_zp, self.out_scale, self.out_zp, len(self.layers),
                                    int(self.double_rounding)], dtype=np.float64)}
        for i, L in enumerate(self.layers):
            arrays |= {f"w{i}": L.w, f"b{i}": L.b, f"m{i}": L.mult, f"s{i}": L.shift,
                       f"z{i}": np.array([L.zp_in, L.zp_out, L.act_min, L.act_max], dtype=np.int32)}
        np.savez(path, **arrays)

    @classmethod
    def load(cls, path: Path = EDGE_NPZ) -> Int8Model:
        d = np.load(path)
        in_scale, in_zp, out_scale, out_zp, n, dbl = d["meta"]
        layers = [QLayer(d[f"w{i}"], d[f"b{i}"], d[f"m{i}"], d[f"s{i}"], *map(int, d[f"z{i}"])) for i in range(int(n))]
        return cls(float(in_scale), int(in_zp), float(out_scale), int(out_zp), layers, bool(dbl))


def tflite_interpreter(path: Path):
    """TFLite interpreter với kernel THAM CHIẾU (không XNNPACK) – cùng loại kernel TFLite Micro dùng."""
    import tensorflow as tf

    it = tf.lite.Interpreter(model_path=str(path),
                             experimental_op_resolver_type=tf.lite.experimental.OpResolverType.BUILTIN_REF)
    it.allocate_tensors()
    return it


def run_tflite(path: Path, xs: np.ndarray) -> np.ndarray:
    """Chạy từng mẫu qua interpreter (input/output đúng kiểu của model: float32 hoặc int8)."""
    it = tflite_interpreter(path)
    inp, out = it.get_input_details()[0], it.get_output_details()[0]
    res = np.empty((len(xs), out["shape"][1]), dtype=out["dtype"])
    for i, x in enumerate(xs):
        it.set_tensor(inp["index"], x[None, :].astype(inp["dtype"]))
        it.invoke()
        res[i] = it.get_tensor(out["index"])[0]
    return res


def extract_int8(path: Path, activations: list[str]) -> Int8Model:
    """Đọc trọng số + tham số lượng tử từ file .tflite INT8 (6 op FULLY_CONNECTED nối tiếp)."""
    it = tflite_interpreter(path)
    tensors = {t["index"]: t for t in it.get_tensor_details()}
    ops = it._get_ops_details()  # API nội bộ nhưng ổn định từ TF 2.x; có kiểm tra cấu trúc bên dưới
    if [o["op_name"] for o in ops] != ["FULLY_CONNECTED"] * len(activations):
        raise ValueError(f"Cấu trúc model không như mong đợi: {[o['op_name'] for o in ops]}")

    def qp(idx):
        q = tensors[idx]["quantization_parameters"]
        return q["scales"].astype(np.float64), q["zero_points"].astype(np.int64)

    layers = []
    for op, act in zip(ops, activations):
        i_in, i_w, i_b = (int(v) for v in op["inputs"])
        i_out = int(op["outputs"][0])
        (s_in,), (z_in,) = qp(i_in)
        s_w, z_w = qp(i_w)
        (s_out,), (z_out,) = qp(i_out)
        if np.any(z_w != 0):
            raise ValueError("Trọng số phải đối xứng (zero_point = 0)")
        # Prepare() của TFLite: effective_scale = input_scale * filter_scale / output_scale (tính bằng double)
        eff = [float(np.float32(s_in)) * float(np.float32(sw)) / float(np.float32(s_out)) for sw in s_w]
        qm = [quantize_multiplier(e) for e in eff]
        act_min = max(-128, int(z_out)) if act == "relu" else -128  # CalculateActivationRangeQuantized
        layers.append(QLayer(
            w=it.get_tensor(i_w).astype(np.int8), b=it.get_tensor(i_b).astype(np.int32),
            mult=np.array([m for m, _ in qm], dtype=np.int32), shift=np.array([s for _, s in qm], dtype=np.int32),
            zp_in=int(z_in), zp_out=int(z_out), act_min=act_min, act_max=127))
    (s_in,), (z_in,) = qp(int(ops[0]["inputs"][0]))
    (s_out,), (z_out,) = qp(int(ops[-1]["outputs"][0]))
    return Int8Model(float(s_in), int(z_in), float(s_out), int(z_out), layers)


# =============================================================================
# Header C cho firmware
# =============================================================================

def _c_arr(ctype: str, name: str, vals, per_line: int = 16) -> str:
    vals = [int(v) for v in np.asarray(vals).ravel()]
    lines = [", ".join(str(v) for v in vals[i:i + per_line]) for i in range(0, len(vals), per_line)]
    return f"static const {ctype} {name}[{len(vals)}] = {{\n    " + ",\n    ".join(lines) + "\n};\n"


def _c_double(name: str, vals) -> str:
    """double 17 chữ số: C++ chuẩn hóa bằng double giống hệt numpy float64 (tránh lệch làm tròn lượng tử)."""
    return f"static const double {name}[{len(vals)}] = {{" + ", ".join(f"{float(v):.17g}" for v in vals) + "};\n"


def render_header(m: Int8Model, mean: np.ndarray, scale: np.ndarray, threshold: float,
                  golden_in: np.ndarray, golden_out: np.ndarray, fingerprint: str) -> str:
    parts = [
        "// =============================================================================",
        "// TỰ SINH bởi `python -m hrc_ai.edge` từ ai_model/models/autoencoder_int8.tflite – KHÔNG SỬA TAY.",
        "// Autoencoder INT8 (per-channel) cho Edge AI trên ESP32 + tham số chuẩn hóa + vector tự kiểm tra.",
        "// =============================================================================",
        "#pragma once",
        "#include <stdint.h>",
        "",
        f'#define HRC_EDGE_FEATURE_FINGERPRINT "{fingerprint}"',
        f"#define HRC_EDGE_N_FEATURES {N_FEATURES}",
        f"#define HRC_EDGE_WINDOW {WINDOW}",
        f"#define HRC_EDGE_JUMP_MMPS {JUMP_MMPS}",
        f"#define HRC_EDGE_TOF_OUT_OF_RANGE_MM {TOF_OUT_OF_RANGE_MM}",
        f"#define HRC_EDGE_N_LAYERS {len(m.layers)}",
        f"#define HRC_EDGE_DOUBLE_ROUNDING {int(m.double_rounding)}",
        f"#define HRC_EDGE_IN_SCALE {m.in_scale:.9g}f",
        f"#define HRC_EDGE_IN_ZP {m.in_zp}",
        f"#define HRC_EDGE_OUT_SCALE {m.out_scale:.9g}f",
        f"#define HRC_EDGE_OUT_ZP {m.out_zp}",
        f"#define HRC_EDGE_THRESHOLD {threshold:.9g}f  // MSE > ngưỡng => bất thường (p99 NORMAL tập val)",
        f"#define HRC_EDGE_N_GOLDEN {len(golden_in)}",
        "",
        "// Chuẩn hóa: x = (đặc trưng - mean) / scale  (StandardScaler học trên NORMAL)",
        _c_double("HRC_EDGE_MEAN", mean),
        _c_double("HRC_EDGE_SCALE", scale),
    ]
    for i, L in enumerate(m.layers):
        parts += [f"// Lớp {i}: {L.w.shape[1]} -> {L.w.shape[0]}, zp_in {L.zp_in}, zp_out {L.zp_out}, "
                  f"kích hoạt [{L.act_min}, {L.act_max}]",
                  _c_arr("int8_t", f"HRC_EDGE_W{i}", L.w), _c_arr("int32_t", f"HRC_EDGE_B{i}", L.b),
                  _c_arr("int32_t", f"HRC_EDGE_M{i}", L.mult), _c_arr("int8_t", f"HRC_EDGE_S{i}", L.shift)]
    parts += ["// Bảng lớp: {n_in, n_out, W, B, M, S, zp_in, zp_out, act_min, act_max}",
              "struct HrcEdgeLayer { uint16_t n_in, n_out; const int8_t* w; const int32_t* b; const int32_t* mult;",
              "                      const int8_t* shift; int32_t zp_in, zp_out, act_min, act_max; };",
              "static const HrcEdgeLayer HRC_EDGE_LAYERS[HRC_EDGE_N_LAYERS] = {"]
    for i, L in enumerate(m.layers):
        parts.append(f"    {{{L.w.shape[1]}, {L.w.shape[0]}, HRC_EDGE_W{i}, HRC_EDGE_B{i}, HRC_EDGE_M{i}, HRC_EDGE_S{i}, "
                     f"{L.zp_in}, {L.zp_out}, {L.act_min}, {L.act_max}}},")
    parts += ["};", "", "// Vector tự kiểm tra: đầu vào int8 -> đầu ra int8 mong đợi (tính bằng TFLite interpreter)",
              _c_arr("int8_t", "HRC_EDGE_GOLDEN_IN", golden_in, 20),
              _c_arr("int8_t", "HRC_EDGE_GOLDEN_OUT", golden_out, 20)]
    return "\n".join(parts)


# =============================================================================
# Xuất
# =============================================================================

def main() -> None:
    import keras
    import tensorflow as tf

    from .dataset import load_split, to_windows
    from .inference import HrcDetector

    det = HrcDetector()
    ae = keras.models.load_model(MODEL_DIR / "autoencoder.keras")
    activations = [layer.activation.__name__ for layer in ae.layers if layer.get_weights()]

    x_tr, _, m_tr = to_windows(load_split("train"))
    x_va, _, m_va = to_windows(load_split("val"))
    rep_x = det.scaler.transform(x_tr[~m_tr["win_anomaly"].to_numpy()]).astype(np.float32)
    val_x = det.scaler.transform(x_va[~m_va["win_anomaly"].to_numpy()]).astype(np.float32)

    # ---- float32 ----
    F32_TFLITE.write_bytes(tf.lite.TFLiteConverter.from_keras_model(ae).convert())

    # ---- INT8 full-integer: dữ liệu đại diện = 500 cửa sổ NORMAL (dải lượng tử theo phân phối bình thường) ----
    def representative():
        for i in np.linspace(0, len(rep_x) - 1, 500).astype(int):
            yield [rep_x[i:i + 1]]

    conv = tf.lite.TFLiteConverter.from_keras_model(ae)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = representative
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    INT8_TFLITE.write_bytes(conv.convert())

    # ---- trích tham số + chọn đúng biến thể làm tròn bằng cách so với interpreter ----
    model = extract_int8(INT8_TFLITE, activations)
    probe = val_x[np.linspace(0, len(val_x) - 1, 400).astype(int)]
    q_probe = model.quantize_input(probe)
    ref = run_tflite(INT8_TFLITE, q_probe)
    for dbl in (True, False):
        model.double_rounding = dbl
        if np.array_equal(model.run(q_probe), ref):
            break
    else:
        raise RuntimeError("Runtime INT8 không khớp TFLite interpreter ở cả 2 biến thể làm tròn")
    print(f"[OK] Runtime INT8 trùng từng bit với TFLite ({'làm tròn kép' if model.double_rounding else 'làm tròn đơn'})")

    threshold = float(np.quantile(model.error(val_x), THRESHOLD_QUANTILE))
    model.save(EDGE_NPZ)
    # Vector tự kiểm tra: vài mẫu NORMAL + 1 mẫu bão hòa (bất thường cực đại)
    golden_in = np.vstack([q_probe[[0, 150, 300]], np.full((1, N_FEATURES), 127, dtype=np.int8)])[:N_GOLDEN]
    golden_out = run_tflite(INT8_TFLITE, golden_in)
    HEADER.write_text(render_header(model, det.scaler.mean_, det.scaler.scale_, threshold, golden_in, golden_out,
                                    feature_fingerprint()), encoding="utf-8", newline="\n")
    meta = {"feature_fingerprint": feature_fingerprint(), "threshold_int8": threshold,
            "double_rounding": model.double_rounding, "activations": activations,
            "in_scale": model.in_scale, "in_zp": model.in_zp, "out_scale": model.out_scale, "out_zp": model.out_zp,
            "feature_names": list(FEATURE_NAMES)}
    EDGE_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    for p in (F32_TFLITE, INT8_TFLITE, EDGE_NPZ, HEADER):
        print(f"     {p.relative_to(ROOT_DIR)}: {p.stat().st_size:,} byte")
    print(f"     Ngưỡng INT8 = {threshold:.4f} (p99 NORMAL val)")


if __name__ == "__main__":
    main()
