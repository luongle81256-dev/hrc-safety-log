// =============================================================================
// HRC Safety Log – cấu hình RIÊNG của firmware (pin mapping, tùy chọn phần cứng)
// Tham số dùng chung (SSM, topic, chu kỳ...) nằm trong hrc_params.h (tự sinh).
// =============================================================================
#pragma once
#include "hrc_params.h"

#define HRC_ROBOT_ID 1u   // phải nằm trong [system].robot_ids của config/hrc_config.toml

// -----------------------------------------------------------------------------
// PIN MAPPING – tự chọn theo chip đích khi build.
// BOARD CHÍNH THỨC CỦA NHÓM 01: ESP32 DevKit V1 (ESP32-WROOM-32), PlatformIO board = esp32dev.
// Nhánh ESP32-S3 chỉ để dự phòng khi đổi board.
// Nguyên tắc: tránh chân strapping, chân flash/PSRAM, chân USB.
// -----------------------------------------------------------------------------
#if defined(CONFIG_IDF_TARGET_ESP32S3)
// [DỰ PHÒNG] ESP32-S3-DevKitC-1: tránh GPIO0/3/45/46 (strapping), 19/20 (USB), 26–37 (flash/PSRAM)
  #define PIN_I2C_SDA      8    // VL53L0X SDA (mặc định Wire trên S3)
  #define PIN_I2C_SCL      9    // VL53L0X SCL
  #define PIN_TOF_XSHUT    10   // VL53L0X XSHUT – reset cứng cảm biến khi treo
  #define PIN_SERVO        4    // SG90 tín hiệu PWM
  #define PIN_RELAY        5    // relay E-Stop (cắt nguồn servo)
  #define PIN_BUZZER       6
  #define PIN_BTN_ESTOP    7    // nút E-Stop vật lý, INPUT_PULLUP, nhấn = LOW
  #define PIN_LED_GREEN    15   // SAFE
  #define PIN_LED_YELLOW   16   // WARNING
  #define PIN_LED_RED      17   // DANGER / LOCKED
#elif defined(CONFIG_IDF_TARGET_ESP32)
// [CHÍNH THỨC] ESP32 DevKit V1 (WROOM-32): tránh GPIO0/2/12/15 (strapping), 6–11 (flash),
// 34–39 chỉ vào và không có pull-up nội
  #define PIN_I2C_SDA      21
  #define PIN_I2C_SCL      22
  #define PIN_TOF_XSHUT    19
  #define PIN_SERVO        18
  #define PIN_RELAY        26
  #define PIN_BUZZER       25
  #define PIN_BTN_ESTOP    33
  #define PIN_LED_GREEN    27
  #define PIN_LED_YELLOW   14
  #define PIN_LED_RED      13
#else
  #error "Chip chưa được hỗ trợ – thêm pin mapping cho chip này trong config.h"
#endif

// -----------------------------------------------------------------------------
// RELAY – thiết kế FAIL-SAFE ("mất điện là dừng"):
//   Nguồn 5V của servo đi qua tiếp điểm NO của relay. Relay chỉ HÚT khi robot
//   được phép chạy. ESP32 treo/reset/mất nguồn => relay nhả => servo mất nguồn.
// Đa số module relay 5V 1 kênh kích mức THẤP (active-LOW) – kiểm tra module thực tế.
// -----------------------------------------------------------------------------
#define RELAY_ACTIVE_LEVEL   LOW
#define RELAY_INACTIVE_LEVEL HIGH

// Servo SG90 (giả định theo datasheet phổ biến: xung 500–2400 us)
#define SERVO_MIN_US   500
#define SERVO_MAX_US   2400
#define SERVO_ARM_MM   100   // chiều dài tay đòn gắn servo – dùng quy đổi deg/s -> mm/s
