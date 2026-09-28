# /iot_code – Tầng IoT

Board chính thức: **ESP32 DevKit V1 (ESP32-WROOM-32)**, PlatformIO `board = esp32dev`, Arduino core 2.0.17 (platform `espressif32@7.1.3`).

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
  ToF mẫu mới (~33 ms)                                       WiFi + NTP + MQTT (LWT)
   └─► SSM -> quyết định -> GHI RELAY NGAY  ◄── lock_request ── LOCK/UNLOCK có ký (HMAC + nonce + NVS)
   └─► servo, LED, buzzer                                     telemetry 10 Hz + heartbeat 2 Hz (có ký)
   └─► snapshot (mutex) ────────────────────────────────────► đọc để gửi
         ▲ link_ok = false khi mất MQTT > 2 s ───────────────┘
```

Mạng chậm hoặc treo **không bao giờ** làm chậm việc ngắt relay. Các trường hợp dẫn tới ngắt relay:
- Chip reset, treo, mất nguồn: relay nhả.
- Task safety treo quá 5 s: watchdog reset chip.
- ToF timeout: coi như có vật ở sát.
- Trạng thái LOCKED: được lưu NVS, nên reset cũng không gỡ được.

## Đã kiểm chứng KHÔNG cần phần cứng

- `pio run`: **build thành công** (RAM 14,1%, Flash 62,4%); code của nhóm biên dịch `-Wall -Wextra` không có cảnh báo.
- `pytest tests/test_firmware_host.py`: logic C++ **giống hệt** simulator Python. Chi tiết:
  - Fast lane khớp từng chu kỳ trên 5 kịch bản, có xen lệnh LOCK/UNLOCK, kèm test riêng cho **giá trị biên**.
  - Kiểm 25 dạng lệnh cho cùng kết quả chấp nhận hay từ chối.
  - JSON và HMAC của telemetry/heartbeat **trùng từng byte** với Python.
  - `seq` qua 3 lần reset cho cùng một dãy.
- Mutation test: cố tình cài 10 lỗi vào logic C++, bộ test **bắt được cả 10**.

## Lắp ráp và nạp firmware (khi có phần cứng)

| Linh kiện | GPIO | Ghi chú |
|---|---|---|
| VL53L0X SDA / SCL | 21 / 22 | VIN 3V3, GND chung |
| VL53L0X XSHUT | 19 | reset cứng khi cảm biến treo |
| Servo SG90 (PWM) | 18 | nguồn 5V **đi qua tiếp điểm NO của relay**, GND chung với ESP32 |
| Relay IN | 26 | module 5V, thường kích mức thấp (`RELAY_ACTIVE_LEVEL` trong config.h) |
| Buzzer (loại active) | 25 | |
| Nút E-Stop | 33 | nối GND khi nhấn (INPUT_PULLUP) |
| LED xanh / vàng / đỏ | 27 / 14 / 13 | nối tiếp điện trở 220–330 Ω |

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
5. **Serial phải hiện:** `[OK] Tự kiểm tra HMAC đạt`. Nếu thấy `[HALT]`, xem lý do in ngay trên dòng đó.
6. **Đo độ trễ (nghiệm thu Phase 5):**
   ```powershell
   pip install -e ".[hw]"
   python scripts/measure_latency.py --port COM5 --duration 60
   ```
   Trong lúc đo: đưa tay vào vùng nguy hiểm nhiều lần, nhấn nút E-Stop, gõ `v 400` để thử vượt tốc. Script in độ trễ xử lý (µs), chu kỳ ToF thật, và độ trễ tệ nhất ước lượng. Sau đó nhóm **cập nhật `t_react_ms` / `t_stop_ms`** trong `config/hrc_config.toml` theo số đo thật, chạy `python scripts/gen_config.py`, rồi huấn luyện lại AI.

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

- **Tần số quyết định:** firmware quyết định theo **mỗi mẫu ToF (~33 ms)**, simulator theo mỗi chu kỳ telemetry (100 ms). Luật là một, chỉ khác tần số; firmware phản ứng nhanh hơn.
- **Tốc độ đo:** SG90 không có encoder, nên `v_meas` là tốc độ đã ra lệnh ở bước trước (giả định).
- **Điều kiện ngắt riêng của firmware:** firmware có thêm nút E-Stop, lỗi cảm biến và mất mạng. Simulator mô phỏng mất mạng bằng `force_estop`.
