"""So sánh autoencoder TRƯỚC / SAU lượng tử hóa: kích thước, thời gian suy luận, độ chính xác.

Cùng giao thức với evaluate.py mục 2 (tập TEST, đáp án "2 s gần nhất có bất thường?", bỏ cửa sổ chứa drift).
Mỗi phiên bản dùng ngưỡng riêng = p99 lỗi tái tạo NORMAL của tập VAL.

Chạy:  python -m hrc_ai.evaluate_edge      (sau `python -m hrc_ai.edge`)
"""
from __future__ import annotations

import json
import time

import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support, roc_auc_score

from hrc_common.config import ROOT_DIR

from .dataset import load_split, to_windows
from .edge import EDGE_META, F32_TFLITE, INT8_TFLITE, THRESHOLD_QUANTILE, Int8Model, run_tflite
from .inference import AE_NPZ_FILE, MODEL_DIR, HrcDetector

REPORT = ROOT_DIR / "ai_model" / "reports" / "edge_metrics.json"


def _metrics(truth: np.ndarray, score: np.ndarray, thr: float) -> dict:
    pred = score > thr
    p, r, f, _ = precision_recall_fscore_support(truth, pred, average="binary", zero_division=0)
    tn, fp, _, _ = confusion_matrix(truth, pred, labels=[False, True]).ravel()
    return {"precision": p, "recall": r, "f1": f, "roc_auc": roc_auc_score(truth, score),
            "false_alarm_rate": fp / max(1, fp + tn), "threshold": thr}


def _time_per_sample(fn, xs: np.ndarray, n: int = 2000) -> float:
    t0 = time.perf_counter()
    fn(xs[:n])
    return (time.perf_counter() - t0) / n * 1e6  # µs/mẫu


def main() -> None:
    det = HrcDetector()
    q = Int8Model.load()
    meta = json.loads(EDGE_META.read_text(encoding="utf-8"))
    x_te, y_te, m_te = to_windows(load_split("test"))
    x_va, _, m_va = to_windows(load_split("val"))
    keep = ~m_te["win_drift"].to_numpy()
    xs = det.scaler.transform(x_te[keep]).astype(np.float32)
    truth = m_te["win_anomaly"].to_numpy()[keep]
    labels = m_te["label"].to_numpy()[keep]
    faults = m_te["fault"].to_numpy()[keep]
    val = det.scaler.transform(x_va[~m_va["win_anomaly"].to_numpy()]).astype(np.float32)

    def f32_err(x):
        out = run_tflite(F32_TFLITE, x)
        return np.mean((out - x) ** 2, axis=1)

    print(f"Tập TEST: {len(xs):,} cửa sổ ({int(truth.sum()):,} có bất thường), bỏ cửa sổ chứa drift")
    errs = {
        "Keras float32": (det.ae.error(xs), det.ae_threshold),
        "TFLite float32": (f32_err(xs), float(np.quantile(f32_err(val), THRESHOLD_QUANTILE))),
        "TFLite INT8": (q.error(xs), meta["threshold_int8"]),
    }
    # Kiểm chứng lại trên TOÀN BỘ tập test: runtime INT8 (numpy, cũng là thuật toán của firmware) == interpreter
    q_in = q.quantize_input(xs)
    exact = np.array_equal(q.run(q_in), run_tflite(INT8_TFLITE, q_in))

    weights_f32 = sum(v.nbytes for k, v in np.load(MODEL_DIR / AE_NPZ_FILE).items() if k[0] in "Wb")
    weights_i8 = sum(L.w.nbytes + L.b.nbytes + L.mult.nbytes + L.shift.nbytes for L in q.layers)
    sizes = {"Keras float32": (MODEL_DIR / "autoencoder.keras").stat().st_size,
             "TFLite float32": F32_TFLITE.stat().st_size, "TFLite INT8": INT8_TFLITE.stat().st_size}
    lat = {"Keras float32": _time_per_sample(det.ae.error, xs),
           "TFLite float32": _time_per_sample(lambda x: run_tflite(F32_TFLITE, x), xs, 500),
           "TFLite INT8": _time_per_sample(lambda x: run_tflite(INT8_TFLITE, q.quantize_input(x)), xs, 500)}
    macs = sum(L.w.size for L in q.layers)

    res: dict = {"test_windows": int(len(xs)), "int8_bit_exact_vs_tflite_full_test": bool(exact), "macs": int(macs),
                 "weights_bytes": {"float32": int(weights_f32), "int8_payload": int(weights_i8)}, "models": {}}
    line = "=" * 92
    print(f"{line}\n {'phiên bản':<16}{'file':>9}{'µs/mẫu*':>9}{'precision':>11}{'recall':>8}{'f1':>8}{'roc_auc':>9}"
          f"{'báo nhầm':>10}{'ngưỡng':>9}\n{line}")
    for name, (err, thr) in errs.items():
        m = _metrics(truth, err, thr)
        by_type = {}
        for g, mask in {"INTRUSION": labels == "INTRUSION", "OVERSPEED": labels == "OVERSPEED",
                        "SENSOR:spike": faults == "spike", "SENSOR:dropout": faults == "dropout"}.items():
            by_type[g] = float((err[mask] > thr).mean()) if mask.any() else None
        res["models"][name] = {**m, "file_bytes": sizes[name], "host_us_per_sample": lat[name], "recall_by_type": by_type}
        print(f" {name:<16}{sizes[name]:>9,}{lat[name]:>9.1f}{m['precision']:>11.4f}{m['recall']:>8.4f}{m['f1']:>8.4f}"
              f"{m['roc_auc']:>9.4f}{m['false_alarm_rate'] * 100:>9.2f}%{thr:>9.3f}")
    d_f = errs["Keras float32"][0] > errs["Keras float32"][1]
    d_q = errs["TFLite INT8"][0] > errs["TFLite INT8"][1]
    res["decision_agreement_float_vs_int8"] = float((d_f == d_q).mean())
    print(line)
    print(f" * thời gian trên MÁY TÍNH (interpreter gọi từng mẫu), chỉ để so tương đối – số đo ESP32 cần board")
    print(f" Quyết định float vs INT8 trùng nhau: {res['decision_agreement_float_vs_int8'] * 100:.2f}% cửa sổ")
    print(f" Trọng số: float32 {weights_f32:,} B -> INT8 {weights_i8:,} B (kể cả bias int32 + hệ số lượng tử) "
          f"= giảm {weights_f32 / weights_i8:.1f} lần | {macs} phép nhân-cộng/lần suy luận")
    print(f" Runtime INT8 (thuật toán của firmware) trùng từng bit TFLite trên toàn bộ tập test: {'CÓ' if exact else 'KHÔNG'}")
    print(" Recall theo loại (INT8): " + ", ".join(f"{k} {v:.3f}" for k, v in res["models"]["TFLite INT8"]["recall_by_type"].items()))
    REPORT.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    print(f" Đã lưu {REPORT.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()
