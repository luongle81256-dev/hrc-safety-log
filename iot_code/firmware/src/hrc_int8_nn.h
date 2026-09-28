// Runtime INT8 cho autoencoder Edge AI – theo ĐÚNG đặc tả lượng tử TFLite
// (reference integer_ops::FullyConnectedPerChannel + MultiplyByQuantizedMultiplier).
// Trùng từng bit với TFLite interpreter (tests/test_edge_ai.py). Không phụ thuộc Arduino.
#pragma once
#include <stdint.h>

#include "hrc_edge_model.h"

namespace hrc {

// int8[20] -> int8[20] qua 6 lớp FULLY_CONNECTED
void edge_nn_run(const int8_t in[HRC_EDGE_N_FEATURES], int8_t out[HRC_EDGE_N_FEATURES]);

struct EdgeResult {
  float error;   // MSE tái tạo (không gian đã chuẩn hóa)
  bool anomaly;  // error > HRC_EDGE_THRESHOLD
};

// Đặc trưng thô -> chuẩn hóa -> lượng tử -> NN -> giải lượng tử -> MSE
EdgeResult edge_infer(const double features[HRC_EDGE_N_FEATURES]);

// Chạy các vector mẫu nhúng trong header, so đầu ra từng bit
bool edge_self_test();

}  // namespace hrc
