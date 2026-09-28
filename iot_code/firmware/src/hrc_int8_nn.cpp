#include "hrc_int8_nn.h"

#include <math.h>
#include <string.h>

namespace hrc {

// MultiplyByQuantizedMultiplier – cùng biến thể làm tròn với TFLite đã sinh model (header ghi rõ)
static inline int32_t mbqm(int32_t x, int32_t qm, int shift) {
#if HRC_EDGE_DOUBLE_ROUNDING
  // gemmlowp: SaturatingRoundingDoublingHighMul rồi RoundingDivideByPOT
  const int left = shift > 0 ? shift : 0, right = shift > 0 ? 0 : -shift;
  const int32_t a = (int32_t)((uint32_t)x << left);
  int32_t high;
  if (a == INT32_MIN && qm == INT32_MIN) {
    high = INT32_MAX;
  } else {
    const int64_t ab = (int64_t)a * qm;
    const int32_t nudge = ab >= 0 ? (1 << 30) : (1 - (1 << 30));
    high = (int32_t)((ab + nudge) / (1LL << 31));
  }
  const int32_t mask = (int32_t)((1LL << right) - 1);
  const int32_t remainder = high & mask;
  const int32_t threshold = (mask >> 1) + (high < 0 ? 1 : 0);
  return (high >> right) + (remainder > threshold ? 1 : 0);
#else
  // TFLITE_SINGLE_ROUNDING: 1 lần làm tròn trên tích 64 bit
  const int total = 31 - shift;
  const int64_t round = (int64_t)1 << (total - 1);
  return (int32_t)(((int64_t)x * qm + round) >> total);
#endif
}

void edge_nn_run(const int8_t in[HRC_EDGE_N_FEATURES], int8_t out[HRC_EDGE_N_FEATURES]) {
  int8_t buf_a[32], buf_b[32];  // lớp rộng nhất = 20
  const int8_t* h = in;
  int8_t* o = buf_a;
  for (int l = 0; l < HRC_EDGE_N_LAYERS; l++) {
    const HrcEdgeLayer& L = HRC_EDGE_LAYERS[l];
    for (int c = 0; c < L.n_out; c++) {
      int32_t acc = L.b[c];
      const int8_t* w = L.w + c * L.n_in;
      for (int d = 0; d < L.n_in; d++) acc += (int32_t)w[d] * ((int32_t)h[d] - L.zp_in);
      acc = mbqm(acc, L.mult[c], L.shift[c]) + L.zp_out;
      if (acc < L.act_min) acc = L.act_min;
      if (acc > L.act_max) acc = L.act_max;
      o[c] = (int8_t)acc;
    }
    h = o;
    o = (o == buf_a) ? buf_b : buf_a;
  }
  memcpy(out, h, HRC_EDGE_N_FEATURES);
}

EdgeResult edge_infer(const double features[HRC_EDGE_N_FEATURES]) {
  float x[HRC_EDGE_N_FEATURES];
  int8_t q[HRC_EDGE_N_FEATURES], y[HRC_EDGE_N_FEATURES];
  for (int i = 0; i < HRC_EDGE_N_FEATURES; i++) {
    // Tái hiện ĐÚNG phía Python: đặc trưng float32 -> StandardScaler tại chỗ trên mảng float32
    // (mỗi phép tính bằng double rồi ép về float32)
    const float f = (float)features[i];
    const float centered = (float)((double)f - HRC_EDGE_MEAN[i]);
    x[i] = (float)((double)centered / HRC_EDGE_SCALE[i]);
    const float v = roundf(x[i] / HRC_EDGE_IN_SCALE);  // làm tròn xa 0, như round_half_away() của Python
    const float qv = v + HRC_EDGE_IN_ZP;
    q[i] = (int8_t)(qv < -128.0f ? -128 : (qv > 127.0f ? 127 : (int)qv));
  }
  edge_nn_run(q, y);
  float err = 0.0f;
  for (int i = 0; i < HRC_EDGE_N_FEATURES; i++) {
    const float r = (float)(y[i] - HRC_EDGE_OUT_ZP) * HRC_EDGE_OUT_SCALE - x[i];  // so với đầu vào float gốc
    err += r * r;
  }
  err /= HRC_EDGE_N_FEATURES;
  return EdgeResult{err, err > HRC_EDGE_THRESHOLD};
}

bool edge_self_test() {
  int8_t out[HRC_EDGE_N_FEATURES];
  for (int g = 0; g < HRC_EDGE_N_GOLDEN; g++) {
    edge_nn_run(HRC_EDGE_GOLDEN_IN + g * HRC_EDGE_N_FEATURES, out);
    if (memcmp(out, HRC_EDGE_GOLDEN_OUT + g * HRC_EDGE_N_FEATURES, HRC_EDGE_N_FEATURES) != 0) return false;
  }
  return true;
}

}  // namespace hrc
