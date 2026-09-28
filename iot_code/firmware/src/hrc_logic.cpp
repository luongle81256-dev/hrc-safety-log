#include "hrc_logic.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

namespace hrc {

const char* trip_reason_name(TripReason r) {
  switch (r) {
    case TRIP_NONE: return "";
    case TRIP_BOOT: return "BOOT";
    case TRIP_DANGER: return "DANGER";
    case TRIP_OVERSPEED: return "OVERSPEED";
    case TRIP_LOCKED: return "LOCKED";
    case TRIP_BUTTON: return "BUTTON";
    case TRIP_SENSOR: return "SENSOR";
    case TRIP_LINK_LOST: return "LINK_LOST";
  }
  return "?";
}

// =============================================================================
// Cảm biến siêu âm HC-SR04
// =============================================================================
RangeSample ultrasonic_range(bool echo_started, bool echo_ended, uint32_t echo_us) {
  RangeSample r;  // gán từng trường: core Arduino 2.x biên dịch gnu++11
  r.distance_mm = TOF_OUT_OF_RANGE_MM;
  r.sensor_ok = echo_started;
  if (!echo_started || !echo_ended) return r;
  const uint64_t mm = (uint64_t)echo_us * SOUND_SPEED_MPS / 2000u;
  if (mm <= HRC_SSM_DISTANCE_VALID_MAX_MM) r.distance_mm = (uint32_t)mm;
  return r;
}

// =============================================================================
// Fast lane
// =============================================================================
FastLane::FastLane(bool locked_at_boot) : locked_(locked_at_boot) {}

void FastLane::trip(TripReason r) {
  estop_ = true;
  reason_ = r;
  has_safe_since_ = false;
}

FastLaneOutput FastLane::step(uint32_t now_ms, uint32_t distance_mm, uint32_t v_cmd_mmps, bool link_ok,
                              bool button_pressed, bool sensor_ok) {
  const bool was_estop = estop_;
  const uint32_t v_meas = v_applied_;
  // Cảm biến lỗi => coi như vật ở sát (khoảng cách 0) => DANGER (nghiêng về an toàn)
  const uint8_t zone = hrc_ssm_classify_zone(sensor_ok ? distance_mm : 0, v_meas);

  // Thứ tự ưu tiên giống SimDevice.tick: DANGER > OVERSPEED > LOCKED; sau đó các điều kiện phần cứng
  if (!sensor_ok) {
    trip(TRIP_SENSOR);
  } else if (zone == HRC_ZONE_DANGER) {
    trip(TRIP_DANGER);
  } else if (v_meas > HRC_SSM_V_ROBOT_LIMIT_MMPS) {
    trip(TRIP_OVERSPEED);
  } else if (locked_) {
    trip(TRIP_LOCKED);
  } else if (button_pressed) {
    trip(TRIP_BUTTON);
  } else if (!link_ok) {
    trip(TRIP_LINK_LOST);
  } else if (estop_) {
    // Chỉ đóng lại relay khi SAFE LIÊN TỤC >= estop_reset_hold_ms (phép trừ uint32 an toàn khi millis() tràn)
    if (zone == HRC_ZONE_SAFE) {
      if (!has_safe_since_) {
        has_safe_since_ = true;
        safe_since_ms_ = now_ms;
      }
      if (now_ms - safe_since_ms_ >= HRC_ESTOP_RESET_HOLD_MS) {
        estop_ = false;
        reason_ = TRIP_NONE;
      }
    } else {
      has_safe_since_ = false;
    }
  }

  if (estop_) {
    v_applied_ = 0;
  } else if (zone == HRC_ZONE_WARNING) {
    v_applied_ = v_cmd_mmps * HRC_WARNING_SPEED_PERCENT / 100u;
  } else {
    v_applied_ = v_cmd_mmps;
  }
  return FastLaneOutput{zone, estop_, v_meas, v_applied_, reason_, estop_ && !was_estop};
}

// =============================================================================
// seq
// =============================================================================
SeqAllocator::SeqAllocator(NvsStore& nvs, uint32_t block) : nvs_(nvs), block_(block) {
  seq_ = nvs_.get_u64("seq_res", 1);
  limit_ = seq_ + block_;
  nvs_.put_u64("seq_res", limit_);  // đặt trước 1 khối ngay khi khởi động
}

uint64_t SeqAllocator::next() {
  const uint64_t s = seq_++;
  if (seq_ >= limit_) {
    limit_ += block_;
    nvs_.put_u64("seq_res", limit_);
  }
  return s;
}

// =============================================================================
// canonical & JSON – số nguyên thập phân, không số 0 ở đầu, phân cách '|'
// =============================================================================
int canon_telemetry(char* buf, size_t n, uint32_t robot_id, uint64_t seq, uint64_t ts_ms, uint32_t distance_mm,
                    uint32_t v_robot_mmps, uint8_t zone, uint8_t estop) {
  return snprintf(buf, n, "%" PRIu32 "|%" PRIu64 "|%" PRIu64 "|%" PRIu32 "|%" PRIu32 "|%u|%u", robot_id, seq, ts_ms,
                  distance_mm, v_robot_mmps, (unsigned)zone, (unsigned)estop);
}

int canon_command(char* buf, size_t n, uint32_t robot_id, const char* cmd, uint64_t nonce) {
  return snprintf(buf, n, "%" PRIu32 "|%s|%" PRIu64, robot_id, cmd, nonce);
}

int canon_heartbeat(char* buf, size_t n, uint32_t robot_id, uint64_t ts_ms, uint8_t state, uint8_t estop,
                    const char* cfg_hash) {
  return snprintf(buf, n, "hb|%" PRIu32 "|%" PRIu64 "|%u|%u|%s", robot_id, ts_ms, (unsigned)state,
                  (unsigned)estop, cfg_hash);
}

int json_telemetry(char* buf, size_t n, uint32_t robot_id, uint64_t seq, uint64_t ts_ms, uint32_t distance_mm,
                   uint32_t v_robot_mmps, uint8_t zone, uint8_t estop, const char* hmac_hex) {
  return snprintf(buf, n,
                  "{\"robot_id\":%" PRIu32 ",\"seq\":%" PRIu64 ",\"ts_ms\":%" PRIu64 ",\"distance_mm\":%" PRIu32
                  ",\"v_robot_mmps\":%" PRIu32 ",\"zone\":%u,\"estop\":%u,\"hmac\":\"%s\"}",
                  robot_id, seq, ts_ms, distance_mm, v_robot_mmps, (unsigned)zone, (unsigned)estop, hmac_hex);
}

int json_heartbeat(char* buf, size_t n, uint32_t robot_id, uint64_t ts_ms, uint8_t state, uint8_t estop,
                   const char* cfg_hash, const char* hmac_hex) {
  return snprintf(buf, n,
                  "{\"robot_id\":%" PRIu32 ",\"ts_ms\":%" PRIu64 ",\"state\":%u,\"estop\":%u,\"cfg\":\"%s\","
                  "\"hmac\":\"%s\"}",
                  robot_id, ts_ms, (unsigned)state, (unsigned)estop, cfg_hash, hmac_hex);
}

int json_lwt(char* buf, size_t n, uint32_t robot_id) {
  return snprintf(buf, n, "{\"robot_id\":%" PRIu32 ",\"online\":0}", robot_id);
}

// =============================================================================
// tiện ích bảo mật
// =============================================================================
void to_hex(const uint8_t* in, size_t len, char* out) {
  static const char* digits = "0123456789abcdef";
  for (size_t i = 0; i < len; i++) {
    out[2 * i] = digits[in[i] >> 4];
    out[2 * i + 1] = digits[in[i] & 0x0F];
  }
  out[2 * len] = '\0';
}

static int hex_val(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool parse_key_hex(const char* hex, uint8_t out[32]) {
  if (hex == nullptr || strlen(hex) != 64) return false;
  uint8_t acc = 0;
  for (size_t i = 0; i < 32; i++) {
    const int hi = hex_val(hex[2 * i]), lo = hex_val(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = (uint8_t)((hi << 4) | lo);
    acc |= out[i];
  }
  return acc != 0;  // khóa mẫu toàn số 0 => từ chối (giống hrc_common.protocol.load_key)
}

bool ct_equal(const uint8_t* a, const uint8_t* b, size_t len) {
  uint8_t diff = 0;
  for (size_t i = 0; i < len; i++) diff |= (uint8_t)(a[i] ^ b[i]);  // không thoát sớm => không lộ thời gian
  return diff == 0;
}

bool is_lower_hex(const char* s, size_t len) {
  if (s == nullptr || strlen(s) != len) return false;
  for (size_t i = 0; i < len; i++) {
    const char c = s[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  }
  return true;
}

}  // namespace hrc
