// =============================================================================
// TỰ SINH từ config/hrc_config.toml bởi scripts/gen_config.py – KHÔNG SỬA TAY.
// Sửa config rồi chạy lại: python scripts/gen_config.py
// =============================================================================
#pragma once
#include <stdint.h>

#define HRC_CONFIG_HASH "40037d15ebf0e786"   // so khớp với backend để phát hiện lệch cấu hình
#define HRC_SSM_PROFILE "demo_tabletop"

// ---- [system] ----
#define HRC_TELEMETRY_PERIOD_MS 100u
#define HRC_HEARTBEAT_PERIOD_MS 500u
#define HRC_FAILSAFE_TIMEOUT_MS 2000u
#define HRC_SEQ_RESERVE_BLOCK   1000u

// ---- [fastlane] / [security] ----
#define HRC_ESTOP_RESET_HOLD_MS   1000u
#define HRC_WARNING_SPEED_PERCENT 50u
#define HRC_CMD_MAX_SKEW_MS       30000u

// ---- [mqtt] ---- (dùng với snprintf(buf, n, FMT, (unsigned)robot_id))
#define HRC_TOPIC_TELEMETRY_FMT "hrc/%u/telemetry"
#define HRC_TOPIC_CMD_FMT       "hrc/%u/cmd"
#define HRC_TOPIC_HEARTBEAT_FMT "hrc/%u/heartbeat"
#define HRC_MQTT_QOS_CMD        1
#define HRC_MQTT_KEEPALIVE_S    2

// ---- [zones] ---- (interface đã chốt)
#define HRC_ZONE_SAFE 0
#define HRC_ZONE_WARNING 1
#define HRC_ZONE_DANGER 2

// ---- [ssm.profiles.demo_tabletop] ----
#define HRC_SSM_V_HUMAN_MMPS 300u
#define HRC_SSM_T_REACT_MS 100u
#define HRC_SSM_T_STOP_MS 150u
#define HRC_SSM_S_STOP_MM 15u
#define HRC_SSM_C_INTRUSION_MM 20u
#define HRC_SSM_Z_ROBOT_MM 5u
#define HRC_SSM_Z_SENSOR_MM 30u
#define HRC_SSM_WARNING_MARGIN_MM 150u
#define HRC_SSM_V_ROBOT_NOMINAL_MMPS 150u
#define HRC_SSM_V_ROBOT_LIMIT_MMPS 250u
#define HRC_SSM_DISTANCE_VALID_MIN_MM 30u
#define HRC_SSM_DISTANCE_VALID_MAX_MM 1200u

// ---- [merkle] / [chain] ---- (firmware không dùng, để tham chiếu)
#define HRC_MERKLE_BATCH_MAX_RECORDS 256u
#define HRC_MAX_VIOLATIONS 3u

// S = ceil((vH*(TR+TS) + vR*TR)/1000) + SS + C + ZR + ZS  (mm)
// PHẢI giống hrc_common/ssm.py. Chặn vR để không tràn uint32.
static inline uint32_t hrc_ssm_protective_distance_mm(uint32_t v_robot_mmps) {
    if (v_robot_mmps > 100000u) v_robot_mmps = 100000u;
    uint32_t dyn = HRC_SSM_V_HUMAN_MMPS * (HRC_SSM_T_REACT_MS + HRC_SSM_T_STOP_MS)
                 + v_robot_mmps * HRC_SSM_T_REACT_MS;
    return (dyn + 999u) / 1000u + HRC_SSM_S_STOP_MM + HRC_SSM_C_INTRUSION_MM
           + HRC_SSM_Z_ROBOT_MM + HRC_SSM_Z_SENSOR_MM;
}

// Biên (d == S) tính là vùng nguy hiểm hơn.
static inline uint8_t hrc_ssm_classify_zone(uint32_t distance_mm, uint32_t v_robot_mmps) {
    uint32_t s = hrc_ssm_protective_distance_mm(v_robot_mmps);
    if (distance_mm <= s) return HRC_ZONE_DANGER;
    if (distance_mm <= s + HRC_SSM_WARNING_MARGIN_MM) return HRC_ZONE_WARNING;
    return HRC_ZONE_SAFE;
}
