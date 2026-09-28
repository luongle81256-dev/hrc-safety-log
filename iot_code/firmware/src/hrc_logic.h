// =============================================================================
// HRC Safety Log – LOGIC THUẦN (không phụ thuộc Arduino/phần cứng)
// Biên dịch được cả trên ESP32 lẫn máy tính (g++) để kiểm chứng đối chiếu với
// simulator Python (tests/test_firmware_host.py). Đặc tả: docs/INTERFACES.md mục 2–6b.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "hrc_params.h"

namespace hrc {

constexpr uint64_t INT63_MAX = 0x7FFFFFFFFFFFFFFFULL;  // giới hạn seq/ts/nonce (docs mục 3)
// Quy ước CHUNG của cả hệ thống cho "không thấy vật / ngoài tầm tin cậy" (giá trị gốc của VL53L0X).
// Simulator, AI, backend đều hiểu số này => cảm biến nào cũng phải trả về đúng giá trị này.
constexpr uint32_t TOF_OUT_OF_RANGE_MM = 8190;

// ---------------- cảm biến siêu âm HC-SR04 ----------------
// Tốc độ âm thanh ~343 m/s ở 20 °C = 0,343 mm/µs; sóng đi rồi dội về nên chia 2:
//   distance_mm = echo_us * 343 / 2000  (làm tròn XUỐNG => khoảng cách nhỏ hơn => nghiêng về an toàn)
// Nóng hơn 20 °C âm đi nhanh hơn => số đo nhỏ hơn thật (an toàn); lạnh hơn thì ngược lại (~0,17 %/°C).
constexpr uint32_t SOUND_SPEED_MPS = 343;
struct RangeSample {
  uint32_t distance_mm;  // mm, hoặc TOF_OUT_OF_RANGE_MM
  bool sensor_ok;        // false => fast lane coi như vật ở sát và NGẮT relay
};
// Đổi kết quả 1 lần đo HC-SR04 sang quy ước hệ thống:
//   echo_started = false : chân Echo không lên HIGH (đứt dây, mất nguồn, module treo) => LỖI cảm biến
//   echo_ended   = false : Echo HIGH quá lâu = không có vật dội lại trong tầm => NGOÀI TẦM (cảm biến vẫn ổn)
//   > distance_valid_max_mm cũng tính là ngoài tầm (giống simulator)
RangeSample ultrasonic_range(bool echo_started, bool echo_ended, uint32_t echo_us);

// Lý do relay đang ngắt (hiển thị Serial/log; KHÔNG gửi trong telemetry)
enum TripReason : uint8_t {
  TRIP_NONE = 0,
  TRIP_BOOT,       // khởi động an toàn
  TRIP_DANGER,     // SSM: người trong khoảng cách bảo vệ
  TRIP_OVERSPEED,  // robot vượt v_robot_limit
  TRIP_LOCKED,     // bị khóa từ trust lane (LOCK)
  TRIP_BUTTON,     // nút E-Stop vật lý
  TRIP_SENSOR,     // cảm biến khoảng cách lỗi (HC-SR04 không phản hồi)
  TRIP_LINK_LOST,  // mất MQTT quá failsafe_timeout
};
const char* trip_reason_name(TripReason r);

struct FastLaneOutput {
  uint8_t zone;         // 0 SAFE, 1 WARNING, 2 DANGER – tính với v_meas
  bool estop;           // true = relay NGẮT
  uint32_t v_meas;      // tốc độ đã áp dụng ở bước trước (giá trị dùng để tính SSM)
  uint32_t v_next;      // tốc độ áp dụng cho bước tới
  TripReason reason;    // lý do ngắt hiện tại (TRIP_NONE nếu đang chạy)
  bool tripped_now;     // bước này vừa chuyển từ chạy -> ngắt
};

// Fast lane: PHẢI có hành vi giống hệt hrc_sim/device.py::SimDevice.tick
class FastLane {
 public:
  explicit FastLane(bool locked_at_boot);
  // Các điều kiện ngoài (link, nút, cảm biến) truyền vào để hàm không phụ thuộc phần cứng.
  FastLaneOutput step(uint32_t now_ms, uint32_t distance_mm, uint32_t v_cmd_mmps, bool link_ok = true,
                      bool button_pressed = false, bool sensor_ok = true);
  void set_locked(bool locked) { locked_ = locked; }
  bool locked() const { return locked_; }
  bool estop() const { return estop_; }
  TripReason reason() const { return reason_; }

 private:
  void trip(TripReason r);
  bool locked_;
  bool estop_ = true;  // khởi động an toàn: relay ngắt cho tới khi SAFE đủ lâu
  TripReason reason_ = TRIP_BOOT;
  bool has_safe_since_ = false;
  uint32_t safe_since_ms_ = 0;
  uint32_t v_applied_ = 0;
};

// NVS trừu tượng (ESP32: Preferences; test: RAM)
class NvsStore {
 public:
  virtual ~NvsStore() = default;
  virtual uint64_t get_u64(const char* key, uint64_t def) = 0;
  virtual void put_u64(const char* key, uint64_t value) = 0;
  virtual bool get_bool(const char* key, bool def) = 0;
  virtual void put_bool(const char* key, bool value) = 0;
};

// seq bền qua reset: đặt trước từng khối (docs mục 6) – giống SimDevice._next_seq
class SeqAllocator {
 public:
  SeqAllocator(NvsStore& nvs, uint32_t block = HRC_SEQ_RESERVE_BLOCK);
  uint64_t next();

 private:
  NvsStore& nvs_;
  uint32_t block_;
  uint64_t seq_;
  uint64_t limit_;
};

// ---------------- chuỗi canonical & JSON (trả về độ dài, <0 hoặc >=n nếu lỗi) ----------------
int canon_telemetry(char* buf, size_t n, uint32_t robot_id, uint64_t seq, uint64_t ts_ms, uint32_t distance_mm,
                    uint32_t v_robot_mmps, uint8_t zone, uint8_t estop);
int canon_command(char* buf, size_t n, uint32_t robot_id, const char* cmd, uint64_t nonce);
int canon_heartbeat(char* buf, size_t n, uint32_t robot_id, uint64_t ts_ms, uint8_t state, uint8_t estop,
                    const char* cfg_hash);
int json_telemetry(char* buf, size_t n, uint32_t robot_id, uint64_t seq, uint64_t ts_ms, uint32_t distance_mm,
                   uint32_t v_robot_mmps, uint8_t zone, uint8_t estop, const char* hmac_hex);
int json_heartbeat(char* buf, size_t n, uint32_t robot_id, uint64_t ts_ms, uint8_t state, uint8_t estop,
                   const char* cfg_hash, const char* hmac_hex);
int json_lwt(char* buf, size_t n, uint32_t robot_id);

// ---------------- tiện ích bảo mật ----------------
void to_hex(const uint8_t* in, size_t len, char* out);          // out cần 2*len+1 byte, chữ thường
bool parse_key_hex(const char* hex, uint8_t out[32]);           // 64 hex -> 32 byte; từ chối khóa toàn 0
bool ct_equal(const uint8_t* a, const uint8_t* b, size_t len);  // so sánh thời gian hằng
bool is_lower_hex(const char* s, size_t len);

}  // namespace hrc
