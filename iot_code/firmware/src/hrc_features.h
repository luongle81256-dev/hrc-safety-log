// Trích 20 đặc trưng từ cửa sổ WINDOW bản ghi – bản C++ của ai_model/hrc_ai/features.py.
// Tính bằng double để khớp numpy float64 (kiểm chứng: tests/test_edge_ai.py).
#pragma once
#include <stdint.h>

#include "hrc_edge_model.h"

namespace hrc {

// Các mảng dài HRC_EDGE_WINDOW, phần tử cuối là bản ghi mới nhất
void compute_features(const uint32_t* distance_mm, const uint32_t* v_robot_mmps, const uint8_t* zone,
                      const uint8_t* estop, double out[HRC_EDGE_N_FEATURES]);

}  // namespace hrc
