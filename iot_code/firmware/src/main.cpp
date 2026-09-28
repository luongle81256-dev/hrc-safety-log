// =============================================================================
// HRC Safety Log – firmware ESP32 DevKit V1
//
// FAST LANE (task "safety", core 1, ưu tiên cao): đọc ToF -> SSM -> NGẮT RELAY ngay, không chờ mạng.
// TRUST LANE (task "net", core 0): WiFi, NTP, MQTT, telemetry/heartbeat có ký HMAC, nhận LOCK/UNLOCK có ký.
// Hai task chỉ trao đổi qua vài biến nguyên tử + 1 snapshot có mutex => mạng chậm/treo KHÔNG làm chậm fast lane.
//
// Fail-safe:
//   - Relay chỉ HÚT khi được phép chạy; ESP32 reset/treo/mất nguồn => relay nhả => servo mất nguồn.
//   - Khởi động ở trạng thái NGẮT; LOCKED lưu NVS nên reset không gỡ được khóa.
//   - Mất MQTT > HRC_FAILSAFE_TIMEOUT_MS => ngắt (tắt bằng HRC_REQUIRE_LINK=0 khi thử trên bàn không có mạng).
//   - ToF timeout => coi như vật ở sát => ngắt. Task an toàn treo > 5 s => watchdog reset chip.
// =============================================================================
#include <Arduino.h>
#include <ESP32Servo.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <VL53L0X.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_task_wdt.h>
#include <sys/time.h>

#include "config.h"
#include "hrc_cmd.h"
#include "hrc_crypto.h"
#include "hrc_features.h"
#include "hrc_int8_nn.h"
#include "hrc_logic.h"
#include "secrets.h"

using namespace hrc;

#ifndef HRC_REQUIRE_LINK
#define HRC_REQUIRE_LINK 1  // 1 = mất MQTT quá failsafe_timeout thì ngắt relay (yêu cầu đồ án)
#endif

// ---------------- NVS bằng Preferences (CHỈ task net ghi) ----------------
class PrefsNvs : public NvsStore {
 public:
  void begin() { prefs_.begin("hrc", false); }
  uint64_t get_u64(const char* k, uint64_t d) override { return prefs_.getULong64(k, d); }
  void put_u64(const char* k, uint64_t v) override { prefs_.putULong64(k, v); }
  bool get_bool(const char* k, bool d) override { return prefs_.getBool(k, d); }
  void put_bool(const char* k, bool v) override { prefs_.putBool(k, v); }

 private:
  Preferences prefs_;
};

// ---------------- trạng thái dùng chung giữa 2 task ----------------
struct Snapshot {
  uint32_t distance_mm = 0;
  uint32_t v_meas = 0;
  uint8_t zone = HRC_ZONE_SAFE;
  bool estop = true;
  bool locked = false;
  TripReason reason = TRIP_BOOT;
};

static PrefsNvs g_nvs;
static SeqAllocator* g_seq = nullptr;
static FastLane* g_fast = nullptr;
static uint8_t g_key[32];
static SemaphoreHandle_t g_mtx;
static Snapshot g_snap;
static volatile bool g_link_ok = !HRC_REQUIRE_LINK;
static volatile int8_t g_lock_request = -1;  // -1: không; 0: UNLOCK; 1: LOCK (net -> safety)
static volatile uint32_t g_v_cmd = HRC_SSM_V_ROBOT_NOMINAL_MMPS;  // đổi bằng lệnh Serial "v <mm/s>" để demo
static uint64_t g_last_nonce = 0;

// Thống kê độ trễ fast lane (µs) – đo từ lúc có mẫu ToF tới lúc đã ghi chân relay
static volatile uint32_t g_lat_max_us = 0, g_lat_count = 0;
static volatile uint64_t g_lat_sum_us = 0;
static volatile uint32_t g_tof_period_sum_us = 0, g_tof_period_n = 0;

// ---------------- Edge AI (autoencoder INT8) – CHỈ cảnh báo sớm, KHÔNG nằm trong vòng an toàn ----------------
struct EdgeRing {
  uint32_t d[HRC_EDGE_WINDOW], v[HRC_EDGE_WINDOW];
  uint8_t z[HRC_EDGE_WINDOW], e[HRC_EDGE_WINDOW];
  uint8_t n = 0;
};
static EdgeRing g_edge_ring;
static bool g_edge_ok = false;  // tự kiểm tra lúc khởi động đạt
static EdgeResult g_edge_last{0.0f, false};
static uint32_t g_edge_runs = 0, g_edge_anomalies = 0, g_edge_us_max = 0;
static uint64_t g_edge_us_sum = 0;

static VL53L0X g_tof;
static Servo g_servo;
static WiFiClient g_net;
static PubSubClient g_mqtt(g_net);
static char g_topic_tel[48], g_topic_cmd[48], g_topic_hb[48];

// ---------------- tiện ích ----------------
static inline void relay_write(bool estop) { digitalWrite(PIN_RELAY, estop ? RELAY_INACTIVE_LEVEL : RELAY_ACTIVE_LEVEL); }

static bool ntp_synced() { return time(nullptr) > 1700000000; }  // sau 11/2023 => đã có giờ thật

static uint64_t unix_ms() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000ULL + (uint64_t)(tv.tv_usec / 1000);
}

[[noreturn]] static void halt(const char* why) {
  relay_write(true);  // luôn ngắt trước
  Serial.printf("[HALT] %s – relay ngắt, dừng hẳn.\n", why);
  for (;;) {
    digitalWrite(PIN_LED_RED, !digitalRead(PIN_LED_RED));
    delay(150);
  }
}

static void set_leds(const FastLaneOutput& o, uint32_t now_ms) {
  const bool blink = (now_ms / 250) % 2;
  digitalWrite(PIN_LED_GREEN, !o.estop && o.zone == HRC_ZONE_SAFE);
  digitalWrite(PIN_LED_YELLOW, o.zone == HRC_ZONE_WARNING);
  digitalWrite(PIN_LED_RED, o.reason == TRIP_LOCKED ? blink : (o.estop || o.zone == HRC_ZONE_DANGER));
  // Buzzer (GIẢ ĐỊNH loại active): kêu ngắt quãng khi người trong vùng nguy hiểm hoặc nhấn nút E-Stop
  digitalWrite(PIN_BUZZER, (o.zone == HRC_ZONE_DANGER || o.reason == TRIP_BUTTON) && blink);
}

// =============================================================================
// FAST LANE
// =============================================================================
static bool tof_begin() {
  digitalWrite(PIN_TOF_XSHUT, LOW);  // reset cứng cảm biến
  delay(10);
  digitalWrite(PIN_TOF_XSHUT, HIGH);
  delay(10);
  g_tof.setTimeout(100);
  if (!g_tof.init()) return false;
  g_tof.setMeasurementTimingBudget(33000);  // ~33 ms/mẫu (GIẢ ĐỊNH – đo lại chu kỳ thật bằng lệnh "stats")
  g_tof.startContinuous();
  return true;
}

static void safety_task(void*) {
  esp_task_wdt_add(nullptr);  // treo > timeout watchdog => reset chip => relay nhả
  float angle = 90.0f;
  int dir = 1;
  uint32_t last_us = micros(), prev_sample_us = 0, consecutive_timeouts = 0;
  for (;;) {
    esp_task_wdt_reset();
    const uint16_t raw = g_tof.readRangeContinuousMillimeters();  // chặn tới khi có mẫu mới
    const uint32_t t0 = micros();
    const bool sensor_ok = !g_tof.timeoutOccurred() && raw != 65535;
    const uint32_t d = raw >= TOF_OUT_OF_RANGE_MM ? TOF_OUT_OF_RANGE_MM : raw;

    const int8_t req = g_lock_request;
    if (req >= 0) {
      g_fast->set_locked(req == 1);
      g_lock_request = -1;
    }
    const bool button = digitalRead(PIN_BTN_ESTOP) == LOW;
    const FastLaneOutput o = g_fast->step(millis(), d, g_v_cmd, g_link_ok, button, sensor_ok);
    relay_write(o.estop);  // <<< HÀNH ĐỘNG AN TOÀN: ngay sau khi quyết định
    const uint32_t t1 = micros();

    // ---- đo đạc ----
    const uint32_t lat = t1 - t0;
    g_lat_sum_us += lat;
    g_lat_count++;
    if (lat > g_lat_max_us) g_lat_max_us = lat;
    if (prev_sample_us) {
      g_tof_period_sum_us += t0 - prev_sample_us;
      g_tof_period_n++;
    }
    prev_sample_us = t0;
    if (o.tripped_now) {
      // Dòng này được scripts/measure_latency.py thu thập
      Serial.printf("LAT,%lu,%lu,%lu,%s\n", (unsigned long)millis(), (unsigned long)lat, (unsigned long)d,
                    trip_reason_name(o.reason));
    }

    // ---- "tay robot": servo quét qua lại với tốc độ đầu tay v_next (mm/s) ----
    const float dt = (t1 - last_us) / 1e6f;
    last_us = t1;
    if (!o.estop && o.v_next > 0) {
      const float deg_per_s = (float)o.v_next / (float)SERVO_ARM_MM * 57.29578f;
      angle += dir * deg_per_s * dt;
      if (angle > 150.0f) { angle = 150.0f; dir = -1; }
      if (angle < 30.0f) { angle = 30.0f; dir = 1; }
      g_servo.write((int)angle);
    }
    set_leds(o, millis());

    xSemaphoreTake(g_mtx, portMAX_DELAY);  // gán từng trường: core Arduino 2.x biên dịch gnu++11
    g_snap.distance_mm = d;
    g_snap.v_meas = o.v_meas;
    g_snap.zone = o.zone;
    g_snap.estop = o.estop;
    g_snap.locked = g_fast->locked();
    g_snap.reason = o.reason;
    xSemaphoreGive(g_mtx);

    // ToF treo nhiều lần liên tiếp => reset cứng cảm biến (relay đã ngắt từ trước)
    consecutive_timeouts = sensor_ok ? 0 : consecutive_timeouts + 1;
    if (consecutive_timeouts >= 5) {
      Serial.println("[TOF] timeout liên tiếp – khởi động lại cảm biến");
      tof_begin();
      consecutive_timeouts = 0;
    }
  }
}

// =============================================================================
// TRUST LANE
// =============================================================================
static Snapshot snapshot() {
  xSemaphoreTake(g_mtx, portMAX_DELAY);
  const Snapshot s = g_snap;
  xSemaphoreGive(g_mtx);
  return s;
}

static void on_mqtt(char* topic, uint8_t* payload, unsigned int len) {
  if (strcmp(topic, g_topic_cmd) != 0) return;
  Command c;
  const CmdResult r = verify_command(payload, len, g_key, HRC_ROBOT_ID, g_last_nonce, ntp_synced(), unix_ms(), &c);
  if (r != CMD_OK) {
    Serial.printf("[CMD] TỪ CHỐI: %s\n", cmd_result_name(r));
    return;
  }
  g_last_nonce = c.nonce;
  g_nvs.put_u64("last_nonce", c.nonce);  // chống phát lại kể cả sau reset
  g_nvs.put_bool("locked", c.lock);      // reset không gỡ được khóa
  g_lock_request = c.lock ? 1 : 0;
  Serial.printf("[CMD] %s hợp lệ (nonce %llu)%s\n", c.lock ? "LOCK" : "UNLOCK", (unsigned long long)c.nonce,
                c.lock ? "" : " – chờ SAFE đủ lâu mới chạy lại");
}

static void publish_telemetry() {
  const Snapshot s = snapshot();
  const uint64_t seq = g_seq->next(), ts = unix_ms();
  char canon[128], mac[65], json[256];
  canon_telemetry(canon, sizeof(canon), HRC_ROBOT_ID, seq, ts, s.distance_mm, s.v_meas, s.zone, s.estop);
  sign_hex(g_key, canon, mac);
  json_telemetry(json, sizeof(json), HRC_ROBOT_ID, seq, ts, s.distance_mm, s.v_meas, s.zone, s.estop, mac);
  g_mqtt.publish(g_topic_tel, json);  // PubSubClient chỉ publish QoS 0
}

static void publish_heartbeat() {
  const Snapshot s = snapshot();
  const uint64_t ts = unix_ms();
  char canon[96], mac[65], json[192];
  canon_heartbeat(canon, sizeof(canon), HRC_ROBOT_ID, ts, s.locked, s.estop, HRC_CONFIG_HASH);
  sign_hex(g_key, canon, mac);
  json_heartbeat(json, sizeof(json), HRC_ROBOT_ID, ts, s.locked, s.estop, HRC_CONFIG_HASH, mac);
  g_mqtt.publish(g_topic_hb, json);
}

// Mỗi chu kỳ telemetry: đưa ảnh chụp vào cửa sổ trượt, đủ HRC_EDGE_WINDOW mẫu thì chạy đặc trưng + INT8.
// Chạy CẢ KHI MẤT MẠNG (giá trị của AI tại biên). Kết quả chỉ in Serial / thống kê.
static void edge_step() {
  if (!g_edge_ok) return;
  const Snapshot s = snapshot();
  EdgeRing& r = g_edge_ring;
  if (r.n == HRC_EDGE_WINDOW) {  // dịch trái 1 (20 phần tử – rẻ hơn quản lý chỉ số vòng)
    memmove(r.d, r.d + 1, sizeof(r.d) - sizeof(r.d[0]));
    memmove(r.v, r.v + 1, sizeof(r.v) - sizeof(r.v[0]));
    memmove(r.z, r.z + 1, sizeof(r.z) - sizeof(r.z[0]));
    memmove(r.e, r.e + 1, sizeof(r.e) - sizeof(r.e[0]));
    r.n--;
  }
  r.d[r.n] = s.distance_mm;
  r.v[r.n] = s.v_meas;
  r.z[r.n] = s.zone;
  r.e[r.n] = s.estop;
  r.n++;
  if (r.n < HRC_EDGE_WINDOW) return;

  const uint32_t t0 = micros();
  double feats[HRC_EDGE_N_FEATURES];
  compute_features(r.d, r.v, r.z, r.e, feats);
  const EdgeResult res = edge_infer(feats);
  const uint32_t us = micros() - t0;
  g_edge_runs++;
  g_edge_us_sum += us;
  if (us > g_edge_us_max) g_edge_us_max = us;
  if (res.anomaly) g_edge_anomalies++;
  if (res.anomaly != g_edge_last.anomaly) {
    // Dòng này được scripts/measure_latency.py thu thập
    Serial.printf("EDGE,%lu,%.3f,%d,%lu\n", (unsigned long)millis(), res.error, (int)res.anomaly, (unsigned long)us);
  }
  g_edge_last = res;
}

static void net_task(void*) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // lưu UTC; ts_ms = Unix epoch ms
  g_mqtt.setServer(MQTT_HOST, MQTT_PORT);
  g_mqtt.setCallback(on_mqtt);
  g_mqtt.setBufferSize(512);
  g_mqtt.setKeepAlive(HRC_MQTT_KEEPALIVE_S);
  g_mqtt.setSocketTimeout(2);
  char lwt[48];
  json_lwt(lwt, sizeof(lwt), HRC_ROBOT_ID);
  char client_id[24];
  snprintf(client_id, sizeof(client_id), "hrc-esp32-%u", (unsigned)HRC_ROBOT_ID);

  uint32_t connected_at = millis(), next_tel = millis(), next_hb = millis(), next_retry = 0;
  bool was_connected = false;
  for (;;) {
    const uint32_t now = millis();
    if (WiFi.status() == WL_CONNECTED && !g_mqtt.connected() && (int32_t)(now - next_retry) >= 0) {
      next_retry = now + 1000;
      const char* user = strlen(MQTT_USER) ? MQTT_USER : nullptr;
      const char* pass = strlen(MQTT_PASS) ? MQTT_PASS : nullptr;
      if (g_mqtt.connect(client_id, user, pass, g_topic_hb, 0, false, lwt)) {
        g_mqtt.subscribe(g_topic_cmd, HRC_MQTT_QOS_CMD);
      }
    }
    g_mqtt.loop();
    const bool connected = g_mqtt.connected();
    if (connected != was_connected) {
      Serial.printf("[NET] MQTT %s\n", connected ? "ĐÃ KẾT NỐI" : "MẤT KẾT NỐI");
      was_connected = connected;
    }
    if (connected) {
      connected_at = now;
      g_link_ok = true;
    } else if (HRC_REQUIRE_LINK && now - connected_at > HRC_FAILSAFE_TIMEOUT_MS) {
      g_link_ok = false;  // fast lane sẽ ngắt relay ở mẫu ToF kế tiếp
    }

    if ((int32_t)(now - next_tel) >= 0) {
      next_tel = (now - next_tel > 1000) ? now + HRC_TELEMETRY_PERIOD_MS : next_tel + HRC_TELEMETRY_PERIOD_MS;
      // Chưa đồng bộ NTP => KHÔNG gửi telemetry (bản ghi sai giờ làm hỏng hộp đen); fast lane vẫn chạy
      if (connected && ntp_synced()) publish_telemetry();
      edge_step();  // cùng nhịp telemetry (10 Hz) như lúc huấn luyện
    }
    if ((int32_t)(now - next_hb) >= 0) {
      next_hb = now + HRC_HEARTBEAT_PERIOD_MS;
      if (connected && ntp_synced()) publish_heartbeat();
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// =============================================================================
// setup / loop
// =============================================================================
void setup() {
  pinMode(PIN_RELAY, OUTPUT);
  relay_write(true);  // VIỆC ĐẦU TIÊN: relay ngắt
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BTN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_TOF_XSHUT, OUTPUT);
  Serial.begin(115200);
  delay(200);
  Serial.printf("\n=== HRC Safety Log – robot %u, config %s, profile %s ===\n", (unsigned)HRC_ROBOT_ID,
                HRC_CONFIG_HASH, HRC_SSM_PROFILE);

  if (!crypto_self_test()) halt("HMAC-SHA256 (mbedtls) KHÔNG khớp test vector");
  if (!parse_key_hex(HRC_HMAC_KEY_HEX, g_key)) halt("HRC_HMAC_KEY_HEX trong secrets.h không hợp lệ (64 hex, khác 0)");
  Serial.println("[OK] Tự kiểm tra HMAC đạt");
  // Edge AI không phải chức năng an toàn: sai thì chỉ TẮT Edge AI, không dừng robot
  g_edge_ok = edge_self_test();
  Serial.printf("[%s] Tự kiểm tra Edge AI INT8 (%d vector mẫu)%s\n", g_edge_ok ? "OK" : "LỖI", HRC_EDGE_N_GOLDEN,
                g_edge_ok ? "" : " – Edge AI bị tắt");

  g_nvs.begin();
  static SeqAllocator seq(g_nvs);
  g_seq = &seq;
  g_last_nonce = g_nvs.get_u64("last_nonce", 0);
  static FastLane fast(g_nvs.get_bool("locked", false));
  g_fast = &fast;
  Serial.printf("[NVS] last_nonce=%llu locked=%d\n", (unsigned long long)g_last_nonce, (int)fast.locked());

  snprintf(g_topic_tel, sizeof(g_topic_tel), HRC_TOPIC_TELEMETRY_FMT, (unsigned)HRC_ROBOT_ID);
  snprintf(g_topic_cmd, sizeof(g_topic_cmd), HRC_TOPIC_CMD_FMT, (unsigned)HRC_ROBOT_ID);
  snprintf(g_topic_hb, sizeof(g_topic_hb), HRC_TOPIC_HEARTBEAT_FMT, (unsigned)HRC_ROBOT_ID);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(400000);
  if (!tof_begin()) Serial.println("[TOF] KHÔNG khởi tạo được VL53L0X – fast lane sẽ ngắt relay (SENSOR)");

  ESP32PWM::allocateTimer(0);
  g_servo.setPeriodHertz(50);
  g_servo.attach(PIN_SERVO, SERVO_MIN_US, SERVO_MAX_US);
  g_servo.write(90);

  g_mtx = xSemaphoreCreateMutex();
  xTaskCreatePinnedToCore(safety_task, "safety", 4096, nullptr, configMAX_PRIORITIES - 2, nullptr, 1);
  xTaskCreatePinnedToCore(net_task, "net", 8192, nullptr, 2, nullptr, 0);
}

// Cổng Serial cho demo: "v <mm/s>" đổi tốc độ lệnh (vd "v 400" để thử vượt tốc), "stats", "s"
void loop() {
  static char line[32];
  static size_t n = 0;
  while (Serial.available()) {
    const char c = (char)Serial.read();
    if (c != '\n' && c != '\r' && n < sizeof(line) - 1) {
      line[n++] = c;
      continue;
    }
    line[n] = '\0';
    n = 0;
    if (line[0] == 'v' && line[1] == ' ') {
      g_v_cmd = (uint32_t)atoi(line + 2);
      Serial.printf("[CMD] tốc độ lệnh = %lu mm/s (giới hạn %u)\n", (unsigned long)g_v_cmd,
                    (unsigned)HRC_SSM_V_ROBOT_LIMIT_MMPS);
    } else if (strcmp(line, "stats") == 0) {
      const uint32_t cnt = g_lat_count ? g_lat_count : 1, pn = g_tof_period_n ? g_tof_period_n : 1;
      Serial.printf("STATS,samples=%lu,lat_mean_us=%lu,lat_max_us=%lu,tof_period_us=%lu\n",
                    (unsigned long)g_lat_count, (unsigned long)(g_lat_sum_us / cnt), (unsigned long)g_lat_max_us,
                    (unsigned long)(g_tof_period_sum_us / pn));
      const uint32_t er = g_edge_runs ? g_edge_runs : 1;
      Serial.printf("EDGESTATS,runs=%lu,us_mean=%lu,us_max=%lu,anomalies=%lu,last_err=%.3f\n",
                    (unsigned long)g_edge_runs, (unsigned long)(g_edge_us_sum / er), (unsigned long)g_edge_us_max,
                    (unsigned long)g_edge_anomalies, g_edge_last.error);
    } else if (strcmp(line, "s") == 0) {
      const Snapshot s = snapshot();
      Serial.printf("d=%lumm v=%lumm/s zone=%u estop=%d(%s) locked=%d link=%d ntp=%d\n",
                    (unsigned long)s.distance_mm, (unsigned long)s.v_meas, s.zone, s.estop,
                    trip_reason_name(s.reason), s.locked, (int)g_link_ok, (int)ntp_synced());
    }
  }
  delay(20);
}
