# Chạy demo trên mạch ESP32 + HC-SR04 thật

Mạch đã lắp theo [sơ đồ đấu dây](so_do_mach.html). Làm lần lượt **Phần 0 → 5**. Mỗi phần có "✅ Đúng khi…"; chưa đúng thì **dừng lại sửa**, đừng làm tiếp.

Dùng **Command Prompt (cmd)**. Các lệnh `pio` chạy trong thư mục `iot_code\firmware`.

---

## Phần 0. Chuẩn bị (1 lần)

### 0.1. Đứng đúng nhánh phần cứng

```bat
cd /d D:\Blockchain\Report_CK
git switch hardware
git branch --show-current
```

✅ Đúng khi in ra `hardware`. Nhánh `main` là bản VL53L0X, **không dùng được** với HC-SR04.

### 0.2. Cắm ESP32 và tìm cổng COM

1. Cắm ESP32 vào laptop bằng **cáp micro-USB có truyền dữ liệu**. Nhiều cáp chỉ sạc được, không nạp code được.
2. Tìm cổng COM:
   ```bat
   cd /d D:\Blockchain\Report_CK\iot_code\firmware
   ..\..\.pio-venv\Scripts\pio.exe device list
   ```
   Hoặc mở *Device Manager → Ports (COM & LPT)*, tìm dòng `Silicon Labs CP210x … (COM5)` hoặc `USB-SERIAL CH340 (COM5)`.

✅ Đúng khi thấy một cổng COM (ví dụ `COM5`). **Ghi lại số cổng.**

❌ Không thấy cổng nào: cài driver **CP210x** (Silicon Labs) hoặc **CH340** (WCH), tùy chip USB in trên board, rồi rút ra cắm lại.

### 0.3. Chọn mạng Wi-Fi

ESP32 và laptop phải **cùng một mạng Wi-Fi**. Mạng đó phải:
- là **2,4 GHz**, vì ESP32 không bắt được 5 GHz;
- **có Internet**, vì ESP32 cần lấy giờ chuẩn (NTP). Chưa có giờ thì ESP32 không gửi dữ liệu và từ chối lệnh LOCK/UNLOCK.

> 💡 **Cách chắc chắn nhất: dùng điện thoại phát hotspot** (bật chế độ 2,4 GHz, hoặc "tối đa tương thích" trên iPhone), cho cả laptop lẫn ESP32 cùng vào. Wi-Fi trường học hoặc công ty thường **chặn các máy nói chuyện với nhau** (client isolation), khiến ESP32 không kết nối được laptop.

---

## Phần 1. Thử mạch trên bàn, chưa cần mạng

**Mục đích:** kiểm tra cảm biến, relay, servo, LED, nút **trước**, khi chưa có Wi-Fi, blockchain hay backend. Lỗi ở đây là lỗi đấu dây, dễ khoanh vùng.

### 1.1. Tạo file bí mật cho firmware

Firmware cần khóa HMAC hợp lệ ngay cả khi thử trên bàn; khóa sai là ESP32 dừng hẳn (`[HALT]`). Kích hoạt `.venv`, rồi thay tên và mật khẩu Wi-Fi ở Phần 0.3:

```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python scripts\make_secrets_h.py --ssid "TenWifi" --password "MatKhauWifi"
```

✅ Đúng khi in ra `[OK] Đã ghi iot_code\firmware\include\secrets.h (MQTT_HOST=…, SSID=…)`.

Kiểm tra `MQTT_HOST` **trùng IP Wi-Fi của laptop**: gõ `ipconfig`, xem dòng *IPv4 Address* dưới mục **Wireless LAN adapter Wi-Fi**. Nếu script lấy nhầm IP (máy có VMware hay VPN thường bị vậy), chạy lại và chỉ định IP:

```bat
python scripts\make_secrets_h.py --ssid "TenWifi" --password "MatKhauWifi" --mqtt-host 192.168.43.10
```

> Mỗi lần đổi mạng Wi-Fi, IP laptop đổi theo, nên phải **chạy lại lệnh này và nạp lại firmware**.

### 1.2. Nạp bản thử trên bàn

```bat
cd /d D:\Blockchain\Report_CK\iot_code\firmware
..\..\.pio-venv\Scripts\pio.exe run -e esp32dev_bench -t upload
```

✅ Đúng khi thấy `[SUCCESS]`.

❌ Nếu báo `Failed to connect to ESP32: Wrong boot mode` hoặc `Timed out waiting for packet header`: chạy lại lệnh, và khi màn hình hiện `Connecting....` thì **nhấn giữ nút BOOT** trên board khoảng 2 giây rồi thả.

### 1.3. Mở Serial Monitor để xem ESP32 "nói" gì

```bat
..\..\.pio-venv\Scripts\pio.exe device monitor
```

Nhấn nút **EN** (reset) trên board để xem lại từ đầu. Phải thấy:

```
=== HRC Safety Log – robot 1, config 426c922f7819b488, profile demo_tabletop ===
[OK] Tự kiểm tra HMAC đạt
[OK] Tự kiểm tra Edge AI INT8 (… vector mẫu)
[NVS] last_nonce=0 locked=0
[OK] HC-SR04 phản hồi, khoảng cách đầu tiên: 845 mm
```

Gõ lệnh vào monitor rồi nhấn Enter:
- `s`: xem trạng thái, ví dụ `d=845mm v=150mm/s zone=0 estop=0() locked=0 link=1 ntp=0`. Ở bản thử trên bàn, `ntp=0` là bình thường.
- `v 400`: thử vượt tốc.
- `v 150`: về tốc độ thường.

Thoát monitor: `Ctrl+C`.

### 1.4. Thử từng phần

| Làm | ✅ Đúng khi |
|---|---|
| Để trống phía trước cảm biến | Sau khoảng 1 giây: **LED xanh**, relay **kêu tách**, servo **quét qua lại** |
| Đưa bàn tay (hoặc tấm bìa) tới khoảng **6–10 cm** | **LED vàng**, servo chậm lại một nửa |
| Đưa tay gần hơn **5 cm** (không sát quá 3 cm) | **LED đỏ + còi kêu**, relay nhả, servo **dừng ngay** |
| Rút tay ra | Sau 1 giây SAFE: servo chạy lại |
| Nhấn nút **E-Stop** | Dừng ngay, còi kêu. Nhả (xoay) nút ra thì chạy lại sau 1 s |
| Gõ `v 400` | Dừng (vượt tốc > 250 mm/s). Gõ `v 150` để chạy lại |
| Gõ `s`, di chuyển tay | Giá trị `d=` thay đổi theo khoảng cách thật (so với thước) |

Các ngưỡng 5 cm và 10 cm là S = 50 mm và S + 50 mm khi robot chạy tốc độ thường (profile `demo_close` trong `config/hrc_config.toml`). Khi robot đã dừng, vùng nguy hiểm co lại còn 2,8 cm. **HC-SR04 đo kém dưới khoảng 3 cm**: đừng để tay chạm sát cảm biến.

**Nếu relay chạy ngược** (tay xa thì nhả, tay gần lại hút): module relay của bạn kích **mức cao**. Sửa [config.h](../iot_code/firmware/include/config.h):
```c
#define RELAY_ACTIVE_LEVEL   HIGH
#define RELAY_INACTIVE_LEVEL LOW
```
Nạp lại (bước 1.2) rồi lưu thay đổi: `git commit -am "Relay kích mức cao"`.

---

## Phần 2. Kết nối ESP32 với laptop qua Wi-Fi

### 2.1. Cho Mosquitto nhận kết nối từ ESP32

Mosquitto cài sẵn trên máy chỉ nhận kết nối **từ chính laptop**. Phải tạm tắt nó và chạy bản nghe cả mạng Wi-Fi.

Mở **cmd bằng quyền Admin** (chuột phải *Command Prompt → Run as administrator*):

```bat
net stop mosquitto
netsh advfirewall firewall add rule name="MQTT 1883" dir=in action=allow protocol=TCP localport=1883
cd /d D:\Blockchain\Report_CK
"C:\Program Files\mosquitto\mosquitto.exe" -c infra\mosquitto\mosquitto.native.conf -v
```

- Lệnh `netsh` (mở tường lửa cổng 1883) **chỉ cần chạy 1 lần**.
- **Để nguyên cửa sổ này**: đây là broker MQTT. ✅ Đúng khi thấy `Opening ipv4 listen socket on port 1883`.

### 2.2. Nạp bản chính thức (có mạng)

Mở cmd khác:

```bat
cd /d D:\Blockchain\Report_CK\iot_code\firmware
..\..\.pio-venv\Scripts\pio.exe run -e esp32dev -t upload
..\..\.pio-venv\Scripts\pio.exe device monitor
```

✅ Đúng khi:
- Serial hiện `[NET] MQTT ĐÃ KẾT NỐI`;
- gõ `s` thấy `link=1 ntp=1`;
- cửa sổ Mosquitto hiện `New client connected from 192.168.x.x as hrc-esp32-1`.

> Ở bản này, **mất kết nối MQTT quá 2 giây là relay ngắt**. Đây là thiết kế an toàn: không có mạng thì robot dừng. Robot không chạy thì kiểm tra `link=1` trước.

Thoát monitor (`Ctrl+C`) trước khi sang Phần 3.

---

## Phần 3. Bật toàn hệ thống

Mở thêm 3 cửa sổ cmd; cửa sổ Mosquitto ở Phần 2 **vẫn giữ nguyên**.

**Cửa sổ A: blockchain**
```bat
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat node
```

**Cửa sổ B: deploy contract, rồi chạy backend**
```bat
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset
cd ..
.venv\Scripts\activate.bat
hrc-backend --batch-interval-ms 8000 --db backend\data\hrc_log_hw.sqlite3
```

`--db …hw.sqlite3` cho phần cứng **database riêng**. Database mô phỏng có số thứ tự bản tin rất cao; nếu dùng chung, backend sẽ loại hết bản tin của ESP32 vì tưởng là tấn công phát lại (`REPLAY`).

✅ Đúng khi backend báo: `Đã kết nối broker localhost:1883` rồi **`Robot 1 ONLINE`**. Khoảng 8 giây sau có dòng `NEO batch robot 1 #1 …`.

**Cửa sổ C: dashboard**
```bat
cd /d D:\Blockchain\Report_CK\dashboard
npm run dev
```

Mở http://localhost:5173 → **Kết nối MetaMask** (tài khoản Hardhat #2, huy hiệu SUPERVISOR).

✅ Đúng khi thẻ **Thiết bị** hiện **ONLINE** và biểu đồ khoảng cách chạy **theo tay bạn**.

---

## Phần 4. Kịch bản demo

| # | Bạn làm | Mạch | Dashboard / backend |
|---|---|---|---|
| 1 | Để trống phía trước | LED xanh, servo quét | SAFE, relay ĐANG CHẠY, batch log được neo mỗi 8 s |
| 2 | Đưa tay vào khoảng 8 cm | LED vàng, servo chậm lại | WARNING |
| 3 | Đưa tay sát cảm biến, giữ khoảng 2 giây | **LED đỏ, còi, servo dừng ngay** | DANGER. Khoảng 1 s sau: backend `SỰ CỐ robot 1: INTRUSION` → `GHI SỰ CỐ on-chain` → `Gửi LOCK`; dashboard **LOCKED** |
| 4 | Rút tay ra | Servo **vẫn đứng yên**, LED đỏ **nhấp nháy** (đang bị khóa) | Serial: `[CMD] LOCK hợp lệ` |
| 5 | Rút điện ESP32 rồi cắm lại | Vẫn khóa: trạng thái đã lưu trong bộ nhớ flash | |
| 6 | Supervisor bấm **Mở khóa (ký bằng MetaMask)** → Xác nhận | Sau khoảng 1 s SAFE: relay đóng, servo chạy lại | ACTIVE; backend `Event Unlocked` → `Gửi UNLOCK` |
| 7 | Nhấn **E-Stop** | Dừng ngay, còi kêu | relay NGẮT. Nút là dừng tại chỗ; nhả nút ra, vùng SAFE 1 s thì chạy lại |

**Không có MetaMask?** Mở khóa bằng lệnh (cmd đã kích hoạt `.venv`):
```bat
python -c "from hrc_backend.chain import ChainClient; print(ChainClient.from_env('SUPERVISOR').unlock(1).tx_hash)"
```

**Màn "sửa log thì bị phát hiện"** (chạy sau vài phút có dữ liệu):
```bat
python scripts\tamper_demo.py --db backend\data\hrc_log_hw.sqlite3 --record-audit
python scripts\verify_log.py --db backend\data\hrc_log_hw.sqlite3
```

---

## Phần 5. Đo độ trễ cho báo cáo

Đóng Serial Monitor trước, vì cổng COM chỉ một chương trình dùng được một lúc. Thay `COM5` bằng cổng của bạn:

```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python scripts\measure_latency.py --port COM5 --duration 60
```

Trong 60 giây: **đưa tay vào vùng nguy hiểm rồi rút ra khoảng 10 lần**, nhấn E-Stop vài lần. Script in ra:
- độ trễ xử lý (µs);
- chu kỳ đo thật của HC-SR04;
- thời gian chờ tiếng vọng;
- độ trễ tệ nhất ước lượng;
- thời gian chạy Edge AI trên ESP32.

**Chép nguyên kết quả vào báo cáo.** Nếu độ trễ tệ nhất lớn hơn `t_react_ms = 150` trong `config/hrc_config.toml`, báo tôi để cập nhật thông số và huấn luyện lại AI.

---

## Kết thúc buổi demo

1. `Ctrl+C` lần lượt ở dashboard, backend, blockchain, và cửa sổ Mosquitto.
2. Bật lại Mosquitto dạng service, trong cmd **Admin**: `net start mosquitto`.
3. Rút USB ESP32 (relay tự nhả, servo mất điện) rồi tắt nguồn 5V ngoài.

---

## Lỗi thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Không thấy cổng COM | Cáp chỉ sạc, hoặc thiếu driver CP210x/CH340 |
| Upload báo `Wrong boot mode` | Giữ nút **BOOT** khi màn hình hiện `Connecting....` |
| `[HALT] HRC_HMAC_KEY_HEX … không hợp lệ` | Chưa chạy `make_secrets_h.py` (bước 1.1) |
| `[US] HC-SR04 KHÔNG phản hồi` | Sai dây TRIG (D21) hoặc ECHO (D22), thiếu 5V ở VCC, hoặc cầu chia áp nối sai. Kiểm theo bảng trong sơ đồ |
| `d=` nhảy lung tung | Cảm biến "thấy" mép bàn hoặc servo: đặt cao lên, hướng thẳng. Thử bằng bàn tay hoặc tấm bìa cứng, đừng dùng vải |
| Báo không phản hồi liên tục khi phía trước trống | Module giữ ECHO quá lâu khi không có vật: đặt một bức tường hoặc tấm bìa cách khoảng 1,5 m |
| Relay chạy ngược | Đổi `RELAY_ACTIVE_LEVEL` (cuối Phần 1) |
| ESP32 tự khởi động lại khi servo chạy | Servo đang lấy điện từ ESP32, hoặc thiếu GND chung. Servo phải lấy từ **nguồn 5V riêng qua relay**, GND nối chung |
| Không bao giờ thấy `MQTT ĐÃ KẾT NỐI` | Wi-Fi 5 GHz; sai SSID/mật khẩu; `MQTT_HOST` sai IP; chưa mở tường lửa; Mosquitto service chưa tắt; mạng trường học chặn → dùng hotspot điện thoại |
| `link=1` nhưng `ntp=0` mãi | Wi-Fi không có Internet, nên ESP32 không lấy được giờ |
| Backend không hiện `Robot 1 ONLINE` | Backend nối `localhost:1883`, phải là **cùng** Mosquitto ở Phần 2 (không để 2 Mosquitto chạy cùng lúc) |
| Backend báo `LOẠI [REPLAY]` liên tục | Đang dùng database cũ. Chạy backend với `--db backend\data\hrc_log_hw.sqlite3`, hoặc một tên file mới |
| Dashboard báo lệch cấu hình (`cfg_match` = false) | Backend hoặc firmware đang ở nhánh `main`. Cả hai phải ở nhánh `hardware`, rồi nạp lại firmware và khởi động lại backend |
| LED đỏ nhấp nháy, robot không chạy dù tay đã rút | Robot đang **LOCKED** trên blockchain. Supervisor phải mở khóa (Phần 4, bước 6) |
