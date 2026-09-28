"""Đánh giá mô hình trên tập TEST (seed chưa từng dùng khi huấn luyện) và xuất báo cáo.

In ra: Precision/Recall/F1 từng nhãn + confusion matrix (RandomForest); P/R/F1 + ROC-AUC phát hiện
bất thường (IsolationForest, Autoencoder); recall theo từng loại lỗi; bài toán "xác nhận DANGER"
so với baseline luật; độ trễ suy luận. Lưu ai_model/reports/{metrics.json, *.png}.

Chạy:  python -m hrc_ai.evaluate
"""
from __future__ import annotations

import json
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)

from hrc_common.config import ROOT_DIR  # noqa: E402
from hrc_sim.device import MemoryNvs, SimDevice  # noqa: E402
from hrc_sim.offline import run_offline  # noqa: E402
from hrc_sim.scenarios import LABELS, make_scenario  # noqa: E402

from .dataset import DATASET_KEY, load_split, to_windows  # noqa: E402
from .inference import HrcDetector  # noqa: E402

REPORT_DIR = ROOT_DIR / "ai_model" / "reports"

# Màu theo bảng tham chiếu của skill dataviz (đã chạy validator: đạt mọi kiểm tra, chế độ sáng)
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES_NORMAL, SERIES_ANOMALY = "#2a78d6", "#eb6834"
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _binary(y_true_anom: np.ndarray, y_pred_anom: np.ndarray, score: np.ndarray) -> dict:
    p, r, f, _ = precision_recall_fscore_support(y_true_anom, y_pred_anom, average="binary", zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y_true_anom, y_pred_anom, labels=[False, True]).ravel()
    return {"precision": p, "recall": r, "f1": f, "roc_auc": roc_auc_score(y_true_anom, score),
            "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
            "false_alarm_rate": fp / max(1, fp + tn)}


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_confusion(cm: np.ndarray, labels: list[str], path) -> None:
    norm = cm / cm.sum(axis=1, keepdims=True)
    fig, ax = plt.subplots(figsize=(6.4, 5.4), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
    ax.pcolormesh(norm, cmap=cmap, vmin=0, vmax=1, edgecolors=SURFACE, linewidth=2)  # khe 2px giữa các ô
    for i in range(len(labels)):
        for j in range(len(labels)):
            dark = norm[i, j] > 0.55
            ax.text(j + 0.5, i + 0.5, f"{norm[i, j] * 100:.1f}%\n({cm[i, j]:,})", ha="center", va="center",
                    fontsize=9, color="#ffffff" if dark else INK)
    ticks = np.arange(len(labels)) + 0.5
    ax.set_xticks(ticks, labels, rotation=20, color=INK_2)
    ax.set_yticks(ticks, labels, color=INK_2)
    ax.invert_yaxis()
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlabel("Dự đoán", color=INK_2)
    ax.set_ylabel("Nhãn thật", color=INK_2)
    ax.set_title("RandomForest – confusion matrix trên tập test (% theo hàng)", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def plot_scores(scores: dict[str, tuple[np.ndarray, float, bool]], is_anom: np.ndarray, path) -> None:
    """Mỗi mô hình 1 ô riêng (thang điểm khác nhau – không gộp 2 trục). Tham số thứ 3: trục x dạng log."""
    fig, axes = plt.subplots(1, len(scores), figsize=(11, 4), facecolor=SURFACE)
    for ax, (name, (score, thr, logx)) in zip(axes, scores.items()):
        _style(ax)
        if logx:  # lỗi tái tạo trải nhiều bậc độ lớn => chia bin theo log
            lo = max(float(np.quantile(score, 0.001)), 1e-4)
            bins = np.geomspace(lo, float(score.max()) * 1.01, 70)
            ax.set_xscale("log")
        else:
            bins = np.linspace(float(score.min()), float(score.max()) * 1.001, 60)
        for mask, color, lab in ((~is_anom, SERIES_NORMAL, "NORMAL"), (is_anom, SERIES_ANOMALY, "Bất thường")):
            ax.hist(np.clip(score[mask], bins[0], bins[-1]), bins=bins, density=True, histtype="step",
                    linewidth=2, color=color, label=lab)
        ax.axvline(thr, color=MUTED, linestyle="--", linewidth=1.5)
        # nhãn ngưỡng đặt về phía có khoảng trống: trái (IF) / phải (AE, ngưỡng nằm sát trục y)
        ax.annotate(f"ngưỡng p99 NORMAL = {thr:.3g}", xy=(thr, 1), xycoords=("data", "axes fraction"),
                    xytext=(6 if logx else -6, -4), textcoords="offset points", ha="left" if logx else "right",
                    va="top", color=INK_2, fontsize=8)
        ax.set_yscale("log")
        ax.set_title(name, color=INK, fontsize=11, loc="left")
        ax.set_xlabel("điểm bất thường (lớn hơn = bất thường hơn)" + (" – thang log" if logx else ""),
                      color=INK_2, fontsize=9)
        ax.set_ylabel("mật độ (log)", color=INK_2, fontsize=9)
    # 1 legend chung phía trên – không đè lên dữ liệu của ô nào
    handles, names = axes[0].get_legend_handles_labels()
    fig.legend(handles, names, loc="upper center", ncol=2, frameon=False, labelcolor=INK_2, fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def measure_latency(det: HrcDetector, n: int = 2000) -> dict:
    dev = SimDevice(1, DATASET_KEY, MemoryNvs())
    tels = [s.telemetry for s in run_offline(make_scenario("mixed", 999), dev, n)]
    times = []
    for t in tels:
        t0 = time.perf_counter()
        det.update(t)
        times.append((time.perf_counter() - t0) * 1000)
    arr = np.asarray(times[25:])  # bỏ các lần đầu (chưa đủ cửa sổ / khởi động)
    return {"mean_ms": float(arr.mean()), "p95_ms": float(np.percentile(arr, 95)), "n": int(arr.size)}


def _row(name: str, m: dict) -> str:
    return (f"  {name:<18}{m['precision']:>9.4f}{m['recall']:>9.4f}{m['f1']:>9.4f}{m['roc_auc']:>9.4f}"
            f"{m['false_alarm_rate'] * 100:>10.2f}%")


def main() -> None:
    det = HrcDetector()
    x, y, meta = to_windows(load_split("test"))
    s = det.score(x)
    labels = [lab for lab in LABELS if lab in det.labels]
    is_drift = meta["is_drift"].to_numpy()
    keep = ~is_drift  # bảng phân loại: bỏ bản ghi drift (không quan sát được – báo cáo riêng ở mục 3)
    win_anom = meta["win_anomaly"].to_numpy()
    keep_win = ~meta["win_drift"].to_numpy()  # bảng bất thường: bỏ cửa sổ chứa drift
    line = "=" * 74
    print(f"{line}\n TẬP TEST: {len(y):,} cửa sổ từ {meta['run_id'].nunique()} lần chạy (seed chưa dùng khi train)")
    print(" " + " | ".join(f"{lab}={int((y == lab).sum()):,}" for lab in labels)
          + f" (trong đó drift={int(is_drift.sum()):,})")

    # ---------- 1. Phân loại 4 nhãn ----------
    yk, pk = y[keep], s["label"][keep]
    rep = classification_report(yk, pk, labels=labels, output_dict=True, zero_division=0)
    cm = confusion_matrix(yk, pk, labels=labels)
    print(f"{line}\n 1. PHÂN LOẠI (RandomForest) – nhãn của bản ghi cuối; không tính drift\n"
          f"  {'nhãn':<14}{'precision':>10}{'recall':>9}{'f1':>9}{'support':>10}")
    for lab in labels:
        r = rep[lab]
        print(f"  {lab:<14}{r['precision']:>10.4f}{r['recall']:>9.4f}{r['f1-score']:>9.4f}{int(r['support']):>10,}")
    print(f"  {'macro avg':<14}{rep['macro avg']['precision']:>10.4f}{rep['macro avg']['recall']:>9.4f}"
          f"{rep['macro avg']['f1-score']:>9.4f}")
    print("  Confusion matrix (hàng = thật, cột = dự đoán):")
    print("  " + " " * 14 + "".join(f"{lab[:10]:>12}" for lab in labels))
    for lab, row in zip(labels, cm):
        print(f"  {lab:<14}" + "".join(f"{v:>12,}" for v in row))

    # ---------- 2. Phát hiện bất thường (nhị phân) ----------
    t, k = win_anom[keep_win], keep_win
    anomaly = {
        "IsolationForest": _binary(t, s["if_anomaly"][k], s["if_score"][k]),
        "Autoencoder": _binary(t, s["ae_anomaly"][k], s["ae_error"][k]),
        "IF ∪ AE": _binary(t, (s["if_anomaly"] | s["ae_anomaly"])[k], np.maximum(
            s["if_score"] / det.if_threshold, s["ae_error"] / det.ae_threshold)[k]),
    }  # RandomForest dự đoán TRẠNG THÁI HIỆN TẠI nên được đánh giá ở mục 1, không so ở đây
    print(f"{line}\n 2. PHÁT HIỆN BẤT THƯỜNG – đáp án: 2 s gần nhất có bất thường? (bỏ cửa sổ chứa drift)")
    print(f"  {'mô hình':<18}{'precision':>9}{'recall':>9}{'f1':>9}{'roc_auc':>9}{'báo nhầm':>11}")
    for name, m in anomaly.items():
        print(_row(name, m))

    # ---------- 3. Recall theo loại bất thường ----------
    groups = {"INTRUSION": y == "INTRUSION", "OVERSPEED": y == "OVERSPEED"}
    for f in ("spike", "dropout", "drift"):
        groups[f"SENSOR:{f}"] = (y == "SENSOR_FAULT") & (meta["fault"].to_numpy() == f)
    per_type = {}
    print(f"{line}\n 3. RECALL THEO LOẠI (tỉ lệ bắt được)\n  {'loại':<16}{'số mẫu':>8}{'IF':>8}{'AE':>8}{'RF đúng lớp':>13}")
    for g, mask in groups.items():
        if not mask.any():
            continue
        truth = "SENSOR_FAULT" if g.startswith("SENSOR") else g
        per_type[g] = {"n": int(mask.sum()), "iforest": float(s["if_anomaly"][mask].mean()),
                       "autoencoder": float(s["ae_anomaly"][mask].mean()),
                       "rf_correct": float((s["label"][mask] == truth).mean())}
        v = per_type[g]
        print(f"  {g:<16}{v['n']:>8,}{v['iforest']:>8.3f}{v['autoencoder']:>8.3f}{v['rf_correct']:>13.3f}")

    # ---------- 4. Xác nhận DANGER ----------
    danger = meta["zone"].to_numpy() == 2
    truth_intr = y[danger] == "INTRUSION"
    ai_intr = s["label"][danger] == "INTRUSION"
    p_ai, r_ai, f_ai, _ = precision_recall_fscore_support(truth_intr, ai_intr, average="binary", zero_division=0)
    base_p = float(truth_intr.mean())
    confirm = {
        "danger_windows": int(danger.sum()),
        "true_intrusions": int(truth_intr.sum()),
        "baseline_rule": {"precision": base_p, "recall": 1.0, "f1": 2 * base_p / (1 + base_p),
                          "false_incidents": int((~truth_intr).sum())},
        "ai": {"precision": p_ai, "recall": r_ai, "f1": f_ai,
               "false_incidents": int((ai_intr & ~truth_intr).sum()),
               "missed_intrusions": int((~ai_intr & truth_intr).sum())},
    }
    print(f"{line}\n 4. XÁC NHẬN DANGER – {confirm['danger_windows']:,} cửa sổ zone=DANGER, "
          f"{confirm['true_intrusions']:,} là người thật")
    print(f"  {'cách':<28}{'precision':>10}{'recall':>9}{'f1':>9}{'sự cố giả':>11}")
    b, a = confirm["baseline_rule"], confirm["ai"]
    print(f"  {'Luật: cứ DANGER là sự cố':<28}{b['precision']:>10.4f}{b['recall']:>9.4f}{b['f1']:>9.4f}"
          f"{b['false_incidents']:>11,}")
    print(f"  {'AI: RF xác nhận INTRUSION':<28}{a['precision']:>10.4f}{a['recall']:>9.4f}{a['f1']:>9.4f}"
          f"{a['false_incidents']:>11,}")
    print(f"  (AI bỏ sót {a['missed_intrusions']} cửa sổ người thật – robot VẪN dừng nhờ fast lane,"
          " chỉ thiếu bản ghi sự cố on-chain)")

    # ---------- 5. Độ trễ ----------
    lat = measure_latency(det)
    print(f"{line}\n 5. ĐỘ TRỄ SUY LUẬN (backend, 1 bản ghi): trung bình {lat['mean_ms']:.2f} ms, "
          f"p95 {lat['p95_ms']:.2f} ms (chu kỳ telemetry 100 ms)\n{line}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    plot_confusion(cm, labels, REPORT_DIR / "confusion_matrix_rf.png")
    plot_scores({"IsolationForest": (s["if_score"][k], det.if_threshold, False),
                 "Autoencoder (lỗi tái tạo)": (s["ae_error"][k], det.ae_threshold, True)}, t,
                REPORT_DIR / "anomaly_scores.png")
    out = {"test_windows": int(len(y)), "drift_windows_excluded": int(is_drift.sum()), "labels": labels,
           "classification": rep, "confusion_matrix": cm.tolist(),
           "anomaly_detection": anomaly, "recall_by_type": per_type, "danger_confirmation": confirm,
           "latency": lat, "thresholds": det.meta["thresholds"], "model_created_at": det.meta["created_at"]}
    (REPORT_DIR / "metrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    print(f"Đã lưu báo cáo: {REPORT_DIR.relative_to(ROOT_DIR)}/(metrics.json, confusion_matrix_rf.png, "
          "anomaly_scores.png)")


if __name__ == "__main__":
    main()
