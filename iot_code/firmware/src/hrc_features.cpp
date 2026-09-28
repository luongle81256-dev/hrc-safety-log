#include "hrc_features.h"

#include <math.h>

#include "hrc_params.h"

namespace hrc {

static double mean_of(const double* a, int n) {
  double s = 0;
  for (int i = 0; i < n; i++) s += a[i];
  return s / n;
}

static double std_of(const double* a, int n) {  // độ lệch chuẩn tổng thể (numpy ddof=0)
  const double m = mean_of(a, n);
  double s = 0;
  for (int i = 0; i < n; i++) s += (a[i] - m) * (a[i] - m);
  return sqrt(s / n);
}

static double median_of(const double* a, int n) {
  double s[HRC_EDGE_WINDOW];
  for (int i = 0; i < n; i++) s[i] = a[i];
  for (int i = 1; i < n; i++) {  // sắp xếp chèn: n = 20
    const double k = s[i];
    int j = i - 1;
    while (j >= 0 && s[j] > k) { s[j + 1] = s[j]; j--; }
    s[j + 1] = k;
  }
  return n % 2 ? s[n / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]);
}

void compute_features(const uint32_t* distance_mm, const uint32_t* v_robot_mmps, const uint8_t* zone,
                      const uint8_t* estop, double out[HRC_EDGE_N_FEATURES]) {
  const int W = HRC_EDGE_WINDOW;
  // Hằng số giống features.py::_constants()
  const double clip = HRC_SSM_DISTANCE_VALID_MAX_MM + 100.0;
  const double abrupt_below = HRC_SSM_DISTANCE_VALID_MAX_MM - 200.0;
  const double dt = HRC_TELEMETRY_PERIOD_MS / 1000.0;

  double D[W], V[W], t[W], diffs[W - 1], resid[W];
  bool O[W];
  double o_sum = 0, v_sum = 0, e_sum = 0, danger = 0, v_max = 0;
  for (int i = 0; i < W; i++) {
    O[i] = distance_mm[i] >= HRC_EDGE_TOF_OUT_OF_RANGE_MM;
    D[i] = distance_mm[i] < clip ? (double)distance_mm[i] : clip;  // 8190 cắt về clip
    V[i] = v_robot_mmps[i];
    t[i] = (i - (W - 1) / 2.0) * dt;
    o_sum += O[i];
    v_sum += V[i];
    e_sum += estop[i];
    danger += zone[i] == 2;
    if (V[i] > v_max) v_max = V[i];
  }
  const double d_mean = mean_of(D, W);
  double tt = 0, num = 0, d_min = D[0], dd_absmax = 0;
  int n_jumps = 0, n_abrupt = 0;
  for (int i = 0; i < W; i++) {
    tt += t[i] * t[i];
    num += (D[i] - d_mean) * t[i];
    if (D[i] < d_min) d_min = D[i];
  }
  const double slope = num / tt;
  for (int i = 0; i < W - 1; i++) {
    diffs[i] = (D[i + 1] - D[i]) / dt;
    const double a = fabs(diffs[i]);
    if (a > dd_absmax) dd_absmax = a;
    if (a > HRC_EDGE_JUMP_MMPS) n_jumps++;
    if (!O[i] && D[i] < abrupt_below && O[i + 1]) n_abrupt++;  // đang ở gần mà mất mục tiêu đột ngột
  }
  for (int i = 0; i < W; i++) resid[i] = D[i] - (d_mean + slope * t[i]);

  out[0] = D[W - 1];
  out[1] = d_min;
  out[2] = d_mean;
  out[3] = std_of(D, W);
  out[4] = slope;
  out[5] = diffs[W - 2];
  out[6] = dd_absmax;
  out[7] = n_jumps;
  out[8] = o_sum / W;
  out[9] = O[W - 1];
  out[10] = n_abrupt;
  out[11] = V[W - 1];
  out[12] = v_max;
  out[13] = v_sum / W;
  out[14] = e_sum / W;
  out[15] = estop[W - 1];
  out[16] = zone[W - 1];
  out[17] = danger / W;
  out[18] = std_of(resid, W);
  out[19] = D[W - 1] - median_of(D, W);
}

}  // namespace hrc
