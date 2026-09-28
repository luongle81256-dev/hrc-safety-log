"""Huấn luyện 3 mô hình và lưu vào ai_model/models/.

    IsolationForest  (không giám sát, học trên NORMAL)  – phát hiện bất thường, kể cả loại chưa từng thấy
    Autoencoder      (không giám sát, học trên NORMAL)  – như trên; đủ nhỏ để lượng tử hóa INT8 lên ESP32 (Phase 7)
    RandomForest     (có giám sát, 4 nhãn)              – xác nhận DANGER là INTRUSION hay SENSOR_FAULT

Ngưỡng bất thường = phân vị 99% điểm bất thường của NORMAL trên tập VAL (≈ 1% báo động nhầm).

Chạy:  python -m hrc_ai.train
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from hrc_common.config import config_hash

from .dataset import load_split, to_windows
from .features import FEATURE_NAMES, N_FEATURES, WINDOW, feature_fingerprint
from .inference import AE_NPZ_FILE, BUNDLE_FILE, META_FILE, MODEL_DIR, NumpyAutoencoder

SEED = 0
THRESHOLD_QUANTILE = 0.99
AE_LAYERS = (16, 8, 4, 8, 16)  # nút cổ chai 4 chiều


def train_autoencoder(x_train: np.ndarray, x_val: np.ndarray, epochs: int):
    import keras  # import muộn: backend không cần TensorFlow

    keras.utils.set_random_seed(SEED)
    layers = [keras.Input(shape=(N_FEATURES,))]
    layers += [keras.layers.Dense(n, activation="relu") for n in AE_LAYERS]
    layers += [keras.layers.Dense(N_FEATURES, activation="linear")]
    model = keras.Sequential(layers, name="hrc_autoencoder")
    model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="mse")
    hist = model.fit(
        x_train, x_train, validation_data=(x_val, x_val), epochs=epochs, batch_size=256, verbose=0,
        callbacks=[keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True)],
    )
    return model, hist.history, keras.__version__


def export_autoencoder_npz(model, path) -> None:
    arrays, acts = {}, []
    dense = [layer for layer in model.layers if layer.get_weights()]  # bỏ lớp không có trọng số (InputLayer...)
    for i, layer in enumerate(dense):
        w, b = layer.get_weights()
        arrays[f"W{i}"], arrays[f"b{i}"] = w.astype(np.float32), b.astype(np.float32)
        acts.append(layer.activation.__name__)
    np.savez(path, n_layers=len(dense), activations=np.array(acts), **arrays)


def main() -> None:
    ap = argparse.ArgumentParser(description="Huấn luyện mô hình AI của HRC Safety Log")
    ap.add_argument("--epochs", type=int, default=60)
    args = ap.parse_args()
    t0 = time.perf_counter()

    x_tr, y_tr, m_tr = to_windows(load_split("train"))
    x_va, y_va, m_va = to_windows(load_split("val"))
    # IF/AE học trên cửa sổ SẠCH HOÀN TOÀN (không bản ghi nào bất thường) – tránh học nhầm vết bất thường là bình thường
    n_tr, n_va = x_tr[~m_tr["win_anomaly"].to_numpy()], x_va[~m_va["win_anomaly"].to_numpy()]
    # Bộ phân loại KHÔNG học drift: 1 cảm biến ToF không phân biệt được "cảm biến lệch chậm" với "người đi chậm"
    # => nhãn đó không học được, chỉ gây báo nhầm. Drift được báo cáo riêng như 1 hạn chế (evaluate.py).
    keep_tr, keep_va = ~m_tr["is_drift"].to_numpy(), ~m_va["is_drift"].to_numpy()
    print(f"Train: {len(x_tr)} cửa sổ ({len(n_tr)} NORMAL sạch, bỏ {int((~keep_tr).sum())} drift khỏi bộ phân loại)"
          f" | Val: {len(x_va)} ({len(n_va)} NORMAL sạch) | {N_FEATURES} đặc trưng, cửa sổ {WINDOW}")

    # Chuẩn hóa theo phân phối NORMAL (cho IF và AE)
    scaler = StandardScaler().fit(n_tr)
    s_tr, s_va = scaler.transform(n_tr).astype(np.float32), scaler.transform(n_va).astype(np.float32)

    # ---- IsolationForest ----
    iforest = IsolationForest(n_estimators=100, max_samples=512, random_state=SEED, n_jobs=-1).fit(s_tr)
    if_thr = float(np.quantile(-iforest.score_samples(s_va), THRESHOLD_QUANTILE))
    print(f"[IsolationForest] ngưỡng = {if_thr:.4f}")

    # ---- Autoencoder ----
    ae, history, keras_version = train_autoencoder(s_tr, s_va, args.epochs)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ae.save(MODEL_DIR / "autoencoder.keras")
    export_autoencoder_npz(ae, MODEL_DIR / AE_NPZ_FILE)
    np_ae = NumpyAutoencoder(MODEL_DIR / AE_NPZ_FILE)
    diff = float(np.abs(np_ae.reconstruct(s_va) - ae.predict(s_va, verbose=0)).max())
    assert diff < 1e-4, f"numpy AE lệch Keras {diff}"
    ae_thr = float(np.quantile(np_ae.error(s_va), THRESHOLD_QUANTILE))
    print(f"[Autoencoder] {len(history['loss'])} epoch, val_loss={min(history['val_loss']):.4f}, "
          f"ngưỡng = {ae_thr:.4f}, numpy vs Keras lệch tối đa {diff:.2e}, {ae.count_params()} tham số")

    # ---- RandomForest ----
    clf = RandomForestClassifier(n_estimators=60, max_depth=12, min_samples_leaf=5,
                                 class_weight="balanced_subsample", n_jobs=-1,
                                 random_state=SEED).fit(x_tr[keep_tr], y_tr[keep_tr])
    print(f"[RandomForest] độ chính xác trên val (không tính drift) = {clf.score(x_va[keep_va], y_va[keep_va]):.4f}")

    joblib.dump({"scaler": scaler, "iforest": iforest, "classifier": clf}, MODEL_DIR / BUNDLE_FILE, compress=3)
    import tensorflow as tf

    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_hash": config_hash(),
        "feature_fingerprint": feature_fingerprint(),
        "window": WINDOW,
        "feature_names": list(FEATURE_NAMES),
        "labels": [str(c) for c in clf.classes_],
        "thresholds": {"iforest": if_thr, "autoencoder": ae_thr, "quantile": THRESHOLD_QUANTILE},
        "autoencoder_layers": [N_FEATURES, *AE_LAYERS, N_FEATURES],
        "autoencoder_params": int(ae.count_params()),
        "train_windows": int(len(x_tr)),
        "anomaly_models_trained_on": "cửa sổ NORMAL sạch hoàn toàn",
        "classifier_excludes": "bản ghi drift (không quan sát được với 1 cảm biến)",
        "val_windows": int(len(x_va)),
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "tensorflow_version": tf.__version__,
        "keras_version": keras_version,
    }
    (MODEL_DIR / META_FILE).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    sizes = {p.name: p.stat().st_size for p in MODEL_DIR.iterdir() if p.is_file()}
    print("Đã lưu:", ", ".join(f"{k} ({v / 1024:.0f} KB)" for k, v in sorted(sizes.items())))
    print(f"Tổng thời gian: {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
