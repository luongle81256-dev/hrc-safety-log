# /iot_code – Tầng IoT

Board chính thức: **ESP32 DevKit V1 (ESP32-WROOM-32)**, PlatformIO `board = esp32dev`, Arduino core 2.0.17 (platform `espressif32@7.1.3`).

> **Nhánh `hardware`: cảm biến siêu âm HC-SR04** thay cho VL53L0X (nhánh `main` giữ bản VL53L0X + simulator).
> Phần đổi: `us_measure()` trong `main.cpp`, hàm quy đổi `ultrasonic_range()` trong `hrc_logic.*` (có test g++),
> chân TRIG/ECHO trong `config.h`, `t_react_ms` 100 → 150 ms, AI huấn luyện lại theo thông số mới.

| Đường dẫn | Nội dung |
|---|---|
| `firmware/src/hrc_logic.*` | **Logic thuần C++**, không phụ thuộc Arduino: fast lane (SSM, E-Stop chốt, giảm tốc), `seq` bền qua reset, chuỗi canonical và JSON |
| `firmware/src/hrc_cmd.*` | Kiểm lệnh LOCK/UNLOCK: HMAC, chống replay, lệch giờ, chặn khóa trùng và escape |
| `firmware/src/hrc_crypto_*.cpp` | HMAC-SHA256 bằng **mbedtls**, kèm tự kiểm tra test vector lúc khởi động |
| `firmware/src/hrc_features.*`, `hrc_int8_nn.*` | **Edge AI**: 20 đặc trưng + autoencoder INT8. Trùng từng bit với TFLite (Phase 7) |
| `firmware/src/main.cpp` | Phần cứng: task **safety** (core 1) và task **net** (core 0, gồm cả Edge AI 10 Hz) |
| `firmware/host_test/` | Chạy logic firmware trên máy tính (g++), dùng cho `tests/test_firmware_host.py` |
| `firmware/include/config.h` | Pin mapping, relay, servo |
| `firmware/include/hrc_params.h` | **Tự sinh** từ config dùng chung. Không sửa tay |
| `firmware/include/hrc_edge_model.h` | **Tự sinh** bởi `python -m hrc_ai.edge` (trọng số INT8, chuẩn hóa, vector tự kiểm tra) |
| `simulator/hrc_sim/` | Simulator Python, là **đặc tả sống** của firmware |

## Kiến trúc firmware

```
core 1  task "safety" (ưu tiên cao, có watchdog)          core 0  task "net"
  HC-SR04 đo mỗi 60 ms                                       WiFi + NTP + MQTT (LWT)
   └─► SSM -> quyết định -> GHI RELAY NGAY  ◄── lock_request ── LOCK/UNLOCK có ký (HMAC + nonce + NVS)
   └─► servo, LED, buzzer                                     telemetry 10 Hz + heartbeat 2 Hz (có ký)
   └─► snapshot (mutex) ────────────────────────────────────► đọc để gửi
         ▲ link_ok = false khi mất MQTT > 2 s ───────────────┘
```

Mạng chậm hoặc treo **không bao giờ** làm chậm việc ngắt relay. Các trường hợp dẫn tới ngắt relay:
- Chip reset, treo, mất nguồn: relay nhả.
- Task safety treo quá 5 s: watchdog reset chip.
- Cảm biến không phản hồi (chân ECHO không lên HIGH: đứt dây, mất nguồn): coi như có vật ở sát.
- Trạng thái LOCKED: được lưu NVS, nên reset cũng không gỡ được.

## Đã kiểm chứng KHÔNG cần phần cứng

- `pio run`: **build thành công** (bản HC-SR04: RAM 14,1%, Flash 61,2%); code của nhóm biên dịch `-Wall -Wextra` không có cảnh báo.
- Quy đổi HC-SR04 (`ultrasonic_range`): test g++ kiểm biên tầm đo, lỗi cảm biến, ngoài tầm, làm tròn xuống (an toàn); cài 4 lỗi đều bị phát hiện.
- `pytest tests/test_firmware_host.py`: logic C++ **giống hệt** simulator Python. Chi tiết:
  - Fast lane khớp từng chu kỳ trên 5 kịch bản, có xen lệnh LOCK/UNLOCK, kèm test riêng cho **giá trị biên**.
  - Kiểm 25 dạng lệnh cho cùng kết quả chấp nhận hay từ chối.
  - JSON và HMAC của telemetry/heartbeat **trùng từng byte** với Python.
  - `seq` qua 3 lần reset cho cùng một dãy.
- Mutation test: cố tình cài 10 lỗi vào logic C++, bộ test **bắt được cả 10**.

## Lắp ráp và nạp firmware (khi có phần cứng)

| Linh kiện | GPIO | Ghi chú |
|---|---|---|
| HC-SR04 VCC / GND | 5V (VIN) / GND | HC-SR04 cần **5V**, GND chung với ESP32 |
| HC-SR04 TRIG | 21 | nối thẳng (3,3 V của ESP32 đủ kích) |
| HC-SR04 ECHO | 22 | ⚠️ **ECHO ra 5 V – PHẢI qua cầu chia áp** (xem sơ đồ dưới), nối thẳng có thể hỏng ESP32 |
| Servo SG90 **180°** (PWM) | 18 | nguồn 5V **đi qua tiếp điểm NO của relay**, GND chung với ESP32 |
| Relay IN | 26 | module 5V, thường kích mức thấp (`RELAY_ACTIVE_LEVEL` trong config.h) |
| Buzzer (loại active) | 25 | |
| Nút E-Stop (nấm đỏ tự giữ, tiếp điểm **NO**) | 33 | nối GND khi nhấn (INPUT_PULLUP) |
| LED xanh / vàng / đỏ | 27 / 14 / 13 | nối tiếp điện trở 220–330 Ω |

**Cầu chia áp cho chân ECHO** (5 V → khoảng 3,3 V):

```
HC-SR04 ECHO ──[ 1 kΩ ]──┬──► GPIO 22 (ESP32)
                         │
                      [ 2 kΩ ]        (không có 2 kΩ: dùng 2 điện trở 1 kΩ nối tiếp, hoặc 1 kΩ + 1,8 kΩ)
                         │
                        GND
```
Điện áp tại GPIO 22 = 5 V × 2 / (1 + 2) ≈ 3,3 V. Loại **HC-SR04P** (chạy được 3,3 V) cấp nguồn 3V3 thì không cần chia áp.

**Lưu ý với HC-SR04:**
- Chùm sóng rộng khoảng 15°: đặt cảm biến **cao hơn mặt bàn** và hướng thẳng, tránh "thấy" mép bàn hoặc servo.
- Vật mềm (vải, áo len) hoặc nghiêng hấp thụ và lệch sóng nên đo kém. Thử bằng **bàn tay hoặc tấm bìa cứng**.
- Vùng mù dưới khoảng 2–3 cm: số đo không tin cậy (hệ thống vẫn coi là rất gần nên vẫn ngắt).
- Một số module rẻ giữ chân ECHO ở HIGH rất lâu khi không có vật phía trước. Firmware coi đó là **lỗi cảm biến** và ngắt relay. Nếu Serial liên tục báo `[US] HC-SR04 không phản hồi` khi phía trước trống, thử đặt một vật (tường, tấm bìa) cách khoảng 1,5 m, hoặc đổi module.

1. **Kiểm tra relay trước khi cắm servo:** đồng hồ vạn năng phải cho thấy tiếp điểm NO **hở** khi ESP32 chưa cấp nguồn.
2. **Bí mật:**
   ```powershell
   python scripts/make_secrets_h.py --ssid "TenWifi" --password "MatKhau"
   ```
   Khóa HMAC được lấy từ `.env`, nên luôn trùng với backend.
3. **Broker nghe trên LAN:** service Mosquitto mặc định chỉ nghe `localhost`. Có 2 cách:
   - PowerShell **Admin**: `net stop mosquitto`, rồi `& "C:\Program Files\mosquitto\mosquitto.exe" -c infra\mosquitto\mosquitto.native.conf -v`
   - Mở tường lửa cổng 1883:
     ```powershell
     New-NetFirewallRule -DisplayName "MQTT 1883" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow
     ```
4. **Nạp firmware** (thư mục `iot_code/firmware`):
   - `..\..\.pio-venv\Scripts\pio.exe run -t upload`
   - Rồi mở Serial: `... device monitor`.
   - Muốn thử fast lane trên bàn khi chưa có mạng thì dùng `-e esp32dev_bench`.
5. **Serial phải hiện:** `[OK] Tự kiểm tra HMAC đạt` và `[OK] HC-SR04 phản hồi, khoảng cách đầu tiên: … mm`. Nếu thấy `[HALT]`, xem lý do in ngay trên dòng đó. Nếu thấy `[US] HC-SR04 KHÔNG phản hồi`, kiểm tra dây TRIG/ECHO, nguồn 5V và cầu chia áp.
6. **Đo độ trễ (nghiệm thu Phase 5):**
   ```powershell
   pip install -e ".[hw]"
   python scripts/measure_latency.py --port COM5 --duration 60
   ```
   Trong lúc đo: đưa tay vào vùng nguy hiểm nhiều lần, nhấn nút E-Stop, gõ `v 400` để thử vượt tốc. Script in độ trễ xử lý (µs), chu kỳ đo thật của cảm biến, thời gian chờ tiếng vọng, và độ trễ tệ nhất ước lượng. Sau đó nhóm **cập nhật `t_react_ms` / `t_stop_ms`** trong `config/hrc_config.toml` theo số đo thật, chạy `python scripts/gen_config.py`, rồi huấn luyện lại AI.

Lệnh Serial dùng khi demo:
- `v <mm/s>`: đổi tốc độ lệnh.
- `s`: xem trạng thái.
- `stats`: xem thống kê độ trễ.

Không có lệnh Serial nào gỡ được khóa: khóa chỉ được gỡ qua lệnh UNLOCK có ký.

## Kịch bản simulator

| Kịch bản | Mô tả | Nhãn sinh ra |
|---|---|---|
| `normal` | Người ở xa 600–1100 mm, thỉnh thoảng ra ngoài tầm đo | NORMAL |
| `approach` | Người đi tới 150–300 mm/s, vào sâu vùng DANGER, dừng 2 s rồi lùi ra | NORMAL, INTRUSION |
| `overspeed` | Cứ 10 s bộ điều khiển lỗi ra lệnh 300–450 mm/s trong 3 s | NORMAL, OVERSPEED |
| `sensor_noise` | Spike, dropout, drift | NORMAL, SENSOR_FAULT |
| `mixed` | Xoay vòng 4 kịch bản trên | cả 4 nhãn |

## Khác biệt đã biết giữa firmware và simulator (có chủ đích)

- **Tần số quyết định:** firmware quyết định theo **mỗi lần đo HC-SR04 (~60 ms)**, simulator theo mỗi chu kỳ telemetry (100 ms). Luật là một, chỉ khác tần số; firmware phản ứng nhanh hơn.
- **Kiểu nhiễu:** simulator mô phỏng nhiễu kiểu ToF (spike, dropout, drift). Nhiễu siêu âm thật (tiếng vọng lạc, vật mềm) khác, nên sau khi có board cần **ghi dữ liệu thật và huấn luyện lại AI**.
- **Tốc độ đo:** SG90 không có encoder, nên `v_meas` là tốc độ đã ra lệnh ở bước trước (giả định).
- **Điều kiện ngắt riêng của firmware:** firmware có thêm nút E-Stop, lỗi cảm biến và mất mạng. Simulator mô phỏng mất mạng bằng `force_estop`.
