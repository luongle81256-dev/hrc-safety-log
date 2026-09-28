# Chạy demo trên mạch thật: ESP32 38 chân + HC-SR04 + blockchain Hardhat local

Quy trình này **đã chạy thành công** trên máy nhóm ngày 28/09/2026. Mỗi bước có 3 phần:
- **Làm gì:** lệnh cần gõ.
- **Vì sao:** bước đó phục vụ phần nào của mô hình.
- **✅ Đúng khi:** dấu hiệu để biết đã làm đúng; chưa đúng thì **dừng lại sửa**, đừng làm tiếp.

Dùng **Command Prompt (cmd)**, trừ khi ghi là *cmd Admin*. Sơ đồ đấu dây: [so_do_mach.html](so_do_mach.html).

> Khi chép lệnh, **chỉ chép các dòng lệnh bên trong khung**, không chép dòng ` ```bat ` và ` ``` ` (chỉ là ký hiệu đánh dấu khung). Gõ **từng dòng một**, mỗi dòng nhấn Enter.

---

## Bức tranh tổng thể: dữ liệu đi đường nào

```
 MẠCH THẬT                               LAPTOP (cùng hotspot Wi-Fi)
 ┌───────────────────────────┐   Wi-Fi   ┌───────────────────────────────────────────────┐
 │ HC-SR04 ──► ESP32 ──► relay ──► servo │ ────────► │ Mosquitto :1883 ──► backend ──► Hardhat node :8545 │
 │   (ESP32 tự dừng robot,   │   MQTT    │  (bưu điện)        (AI,         (blockchain,       │
 │    không chờ ai)          │ ◄──────── │                     Merkle)      smart contract)   │
 └───────────────────────────┘ LOCK/UNLOCK│  dashboard (trình duyệt) + MetaMask (ví supervisor) │
                                          └───────────────────────────────────────────────┘
```

Ghi nhớ 3 ý khi thuyết trình:
1. **ESP32 tự dừng robot tại chỗ** khi tay vào dưới 5 cm. Không cần mạng, không chờ blockchain.
2. **Blockchain làm "hộp đen + trọng tài":** ghi bằng chứng, khóa robot, và **chỉ SUPERVISOR** mới mở được.
3. **ESP32 không nói chuyện trực tiếp với blockchain.** Nó chỉ gửi và nhận tin qua Mosquitto; backend là cầu nối. Vì vậy blockchain chạy `localhost` trên laptop là đủ.

---

## PHẦN A. Cài đặt: chỉ làm 1 lần (nhóm đã làm xong)

Phần này để tham khảo, **không cần làm lại**. Chỉ làm lại khi đổi máy, đổi board, hoặc đổi mạng Wi-Fi (mục A4).

| # | Đã làm | Vì sao |
|---|---|---|
| A1 | Cài **driver CP210x** (Silicon Labs) | Board dùng chip USB CP2102. Thiếu driver thì Windows không tạo cổng COM, không nạp code được. Board hiện ở **COM9** |
| A2 | `git switch hardware` | Nhánh `hardware` là bản cho HC-SR04 + ngưỡng 5 cm. Nhánh `main` là bản mô phỏng (VL53L0X) |
| A3 | Mở tường lửa cổng 1883 (lệnh `netsh …` trong cmd Admin) | Cho ESP32 gửi tin tới Mosquitto trên laptop. Windows chặn kết nối từ ngoài vào |
| A4 | Tạo `secrets.h` + nạp firmware có mạng (`-e esp32dev`) | Tên, mật khẩu Wi-Fi và IP laptop được **ghi thẳng vào firmware**. ESP32 không có màn hình để chọn Wi-Fi |
| A5 | Cài **MetaMask**, nhập tài khoản Hardhat #2 (`0x3C44…93BC`) | Đây là ví SUPERVISOR, **người duy nhất được mở khóa robot** |

**Khi nào phải làm lại A4?** Khi đổi sang mạng Wi-Fi khác, hoặc IP laptop thay đổi (xem bước B1). Cách làm ở Phụ lục 1.

---

## PHẦN B. Chạy demo từ đầu (mỗi buổi)

Cần **6 cửa sổ**: 1 cmd Admin + 4 cmd thường + trình duyệt. Làm **đúng thứ tự** B1 → B8.

### B1. Nối mạng: laptop vào hotspot điện thoại

**Làm gì:**
1. Điện thoại bật **hotspot "Luonglee"** (iPhone: bật **"Tối đa hóa tính tương thích"** để phát 2,4 GHz).
2. Laptop kết nối Wi-Fi **Luonglee**.
3. Mở cmd, gõ `ipconfig`. Xem dòng **IPv4 Address** dưới mục **Wireless LAN adapter Wi-Fi**.

**Vì sao:**
- ESP32 và laptop phải **cùng một mạng** mới gửi tin cho nhau được.
- ESP32 chỉ bắt được **2,4 GHz**.
- Wi-Fi công cộng (quán cà phê, cửa hàng, trường học) thường **chặn các máy nói chuyện với nhau**, nên dùng hotspot riêng.
- Hotspot phải có **4G/Internet**: ESP32 cần lấy **giờ chuẩn** để ký tên thời gian cho dữ liệu và kiểm tra lệnh LOCK/UNLOCK.

**✅ Đúng khi:** IPv4 là **172.20.10.5**, trùng IP đã ghi trong firmware. Nếu **khác**, làm Phụ lục 1 trước khi tiếp tục.

---

### B2. Bật "bưu điện" MQTT cho ESP32 (cửa sổ 1, **cmd Admin**)

**Làm gì:** chuột phải *Command Prompt* → **Run as administrator**, rồi gõ:
```bat
net stop mosquitto
cd /d D:\Blockchain\Report_CK
"C:\Program Files\mosquitto\mosquitto.exe" -c infra\mosquitto\mosquitto.native.conf -v
```
**Để nguyên cửa sổ này** suốt buổi demo.

**Vì sao:**
- **Mosquitto** là "bưu điện" chuyển tin giữa ESP32 và backend.
- Bản cài sẵn chạy ngầm (service) **chỉ nhận thư từ chính laptop**, nên phải tắt nó (`net stop`), rồi chạy bản cấu hình `mosquitto.native.conf`, **nhận thư từ cả mạng Wi-Fi**.
- Phải là cmd Admin vì tắt service cần quyền quản trị.

> ⚠️ **Chỉ được có MỘT Mosquitto.** Nếu service chạy ngầm còn bật (hoặc tự bật lại), ESP32 vào Mosquitto của bạn còn backend vào service. Hai bên không thấy nhau, dashboard báo **OFFLINE** dù ESP32 đã kết nối. Kiểm tra trong cmd Admin:
> ```bat
> sc query mosquitto
> ```
> Dòng `STATE` phải là **`1  STOPPED`**. Nếu là `RUNNING`, chạy `net stop mosquitto` rồi kiểm tra lại.

**✅ Đúng khi:** thấy `Opening ipv4 listen socket on port 1883`. Vài giây sau khi ESP32 có điện, sẽ thấy thêm:
```
New client connected from 172.20.10.10 as hrc-esp32-1
```
Đây là ESP32 của bạn: nó lấy IP 172.20.10.10 trên hotspot.

---

### B3. Cấp điện cho mạch

**Làm gì:**
1. Cắm **USB** của ESP32 vào laptop.
2. Bật **nguồn 5V ngoài** cho servo.
3. Để **trống phía trước cảm biến** ít nhất 15 cm.

**Vì sao:**
- Firmware **đã nạp sẵn** trong ESP32, nên cắm điện là nó tự chạy: tự vào Wi-Fi, tự kết nối Mosquitto. **Không cần nạp lại code mỗi buổi.**
- Phía trước phải trống, vì robot chỉ được chạy khi **vùng an toàn (SAFE) liên tục 1 giây**.

**✅ Đúng khi:**
- cửa sổ Mosquitto (B2) hiện `New client connected … as hrc-esp32-1`;
- khoảng 1 giây sau: **relay kêu "tách", LED xanh, servo bắt đầu quay**.

Trước khi nối được Mosquitto, relay **ngắt** và servo **đứng yên**. Đó là đúng thiết kế: **mất mạng quá 2 giây là tự dừng** (lý do `LINK_LOST`). Nếu servo không quay, xem Phụ lục 2.

---

### B4. Bật blockchain (cửa sổ 2)

**Làm gì:**
```bat
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat node
```
**Để nguyên cửa sổ này.**

**Vì sao:** lệnh này dựng một **blockchain Ethereum chạy ngay trên laptop** (địa chỉ `127.0.0.1:8545`), có sẵn 20 ví, mỗi ví 10.000 ETH thử nghiệm. Nó thay cho mạng Ethereum thật trong lúc demo: nhanh, miễn phí, chỉ tồn tại khi cửa sổ này còn mở.

**✅ Đúng khi:** thấy `Started HTTP and WebSocket JSON-RPC server at http://127.0.0.1:8545/`.

> Nếu báo `EADDRINUSE … 8545`: đã có blockchain chạy sẵn từ trước. Dùng luôn nó, bỏ qua bước này.

---

### B5. Đưa smart contract lên blockchain, rồi bật backend (cửa sổ 3)

**Làm gì:**
```bat
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset
cd ..
.venv\Scripts\activate.bat
hrc-backend --batch-interval-ms 8000 --db backend\data\hrc_log_hw.sqlite3
```
**Để nguyên cửa sổ này.**

**Vì sao, từng lệnh:**

| Lệnh | Làm gì | Vì sao |
|---|---|---|
| `ignition deploy … --reset` | **Deploy** contract `HRCSafetyLog`, cấp 4 vai trò (DEVICE, SUPERVISOR, OPERATOR, AUDITOR), đăng ký robot 1 | Blockchain ở B4 **mới tinh, trống trơn** mỗi lần bật. Phải đưa contract lên lại |
| `activate.bat` | Bật môi trường Python của dự án | Để gõ được lệnh `hrc-backend` |
| `hrc-backend …` | Chạy backend: nhận tin từ ESP32 → kiểm chữ ký HMAC → AI phân tích → ghi log → neo lên blockchain → nghe blockchain để gửi LOCK/UNLOCK xuống ESP32 | Đây là **cầu nối** giữa mạch và blockchain |
| `--batch-interval-ms 8000` | Cứ **8 giây** gom log thành một batch, tính Merkle root, ghi lên chuỗi | Mặc định 30 giây; để 8 giây cho demo thấy nhanh |
| `--db …hrc_log_hw.sqlite3` | Dùng **database riêng** cho phần cứng | Tách khỏi log mô phỏng. Dùng chung thì backend tưởng bản tin ESP32 là tấn công phát lại (`REPLAY`) và loại hết |

**✅ Đúng khi:** cửa sổ 3 lần lượt hiện:
```
successfully deployed 🚀                       ← contract đã lên chuỗi
Mạng local (chain 31337), contract 0x5FbDB…    ← backend thấy contract
Đã kết nối broker localhost:1883               ← backend nối được Mosquitto
Robot 1 ONLINE                                 ← backend thấy ESP32
NEO batch robot 1 #1: … bản ghi … tx=0x…       ← log đầu tiên đã được niêm phong trên blockchain
```
Servo vẫn quay như ở B3. Backend không điều khiển trực tiếp robot; nó chỉ ghi chép, phân tích, và gửi lệnh LOCK/UNLOCK theo blockchain.

Nếu thấy `DB … thuộc contract khác – đã cất sang …` thì đó là **bình thường**: contract mới nên log cũ được cất sang file khác, không bị xóa.

---

### B6. Bật dashboard (cửa sổ 4)

**Làm gì:**
```bat
cd /d D:\Blockchain\Report_CK\dashboard
npm run dev
```
Mở trình duyệt (**Chrome có MetaMask**) vào **http://localhost:5173**.

**Vì sao:** dashboard là màn hình theo dõi.
- Nó nhận số đo **real-time** từ backend.
- Riêng trạng thái ACTIVE/LOCKED và lịch sử sự cố thì nó **đọc thẳng từ blockchain**, không tin backend. Đây là ý nghĩa của "minh bạch".

**✅ Đúng khi:**
- 3 chấm xanh ở trên: **Backend real-time · MQTT · Hardhat local**;
- thẻ **Thiết bị: ONLINE**, vùng **SAFE**;
- biểu đồ chạy theo tay bạn.

---

### B7. Kết nối ví SUPERVISOR

**Làm gì:**
1. Mở MetaMask, chọn **Imported Account 1 (0x3C44C…93BC)** và mạng **Hardhat local**.
2. **Chỉ khi vừa bật lại blockchain ở B4:** MetaMask → **Cài đặt → Nâng cao → Xóa dữ liệu thẻ hoạt động** (*Clear activity tab data*).
3. Trên dashboard bấm **Kết nối MetaMask** → **Kết nối**.

**Vì sao:**
- Chỉ ví có vai trò **SUPERVISOR** mới gọi được hàm `unlock()` của contract. Contract tự kiểm tra, không ai lách được.
- Bước xóa dữ liệu hoạt động là vì blockchain mới bắt đầu lại từ số 0, nhưng MetaMask vẫn nhớ số thứ tự giao dịch cũ. Không xóa sẽ gặp lỗi `nonce too high` khi bấm Mở khóa.

**✅ Đúng khi:** góc trên dashboard hiện huy hiệu **`0x3C44…93BC SUPERVISOR`**.

---

### B8. Trình diễn

| # | Bạn làm | Mạch | Dashboard / backend | Giải thích khi thuyết trình |
|---|---|---|---|---|
| 1 | Để trống phía trước | LED xanh, servo quay | **SAFE**, relay ĐANG CHẠY; cứ 8 s có dòng `NEO batch` | Log liên tục được niêm phong bằng **Merkle root**: chỉ 32 byte/batch lên chuỗi, rẻ hơn ghi từng dòng khoảng 256 lần |
| 2 | Đưa tay vào **6–10 cm** | LED vàng, servo **chậm một nửa** | **WARNING** | Vùng cảnh báo: robot giảm tốc trước khi phải dừng |
| 3 | Đưa tay vào **3–5 cm**, giữ 1–2 giây | **LED đỏ, còi kêu, servo dừng ngay** | **DANGER**. Khoảng 1 s sau: backend `SỰ CỐ robot 1: INTRUSION` → `GHI SỰ CỐ on-chain` → `Gửi LOCK`; dashboard **LOCKED** | **ESP32 tự dừng robot ngay** (fast lane). Sau đó AI xác nhận là người thật, contract ghi sự cố và khóa robot (trust lane) |
| 4 | Rút tay ra | Servo **vẫn đứng yên**, LED đỏ **nhấp nháy** | vẫn **LOCKED** | Robot bị khóa **trên blockchain**, người rời đi cũng không tự chạy lại |
| 5 | (tùy chọn) Rút USB ESP32, cắm lại | Vẫn khóa | | Trạng thái khóa lưu trong bộ nhớ flash: rút điện cũng không gỡ được |
| 5b | Thử mở khóa bằng ví **OPERATOR** (cửa sổ cmd, xem lệnh dưới bảng) | Servo **vẫn đứng yên** | Lỗi `AccessControlUnauthorizedAccount`; vẫn **LOCKED** | **Chỉ SUPERVISOR mở được.** Contract chặn ngay, kể cả người gọi thẳng blockchain (không qua giao diện). Admin cũng bị từ chối |
| 6 | Bấm **Mở khóa (ký bằng MetaMask)** → **Xác nhận** | 1–2 s sau: relay tách, **servo quay lại** | **ACTIVE**, dòng *"Đã mở khóa – giao dịch 0x…"* | Chỉ SUPERVISOR mở được, và **giao dịch ghi vĩnh viễn ai mở, lúc nào** |
| 7 | Nhấn nút **E-Stop** | Dừng ngay, còi kêu | relay NGẮT | Dừng khẩn cấp bằng tay; xoay nút nhả ra thì chạy lại sau 1 s SAFE |

**Lệnh cho bước 5b** (cmd, khi robot đang LOCKED):
```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python -c "from hrc_backend.chain import ChainClient; ChainClient.from_env('OPERATOR').unlock(1)"
```
✅ Đúng khi báo `unlock bị từ chối: AccessControlUnauthorizedAccount(account=0x90f7…, …)` và servo không chạy lại. Đã thử trên mạch thật ngày 28/09/2026.

Muốn diễn trên MetaMask: nhập ví OPERATOR (Hardhat #3, khóa thử nghiệm công khai `0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6`), chọn tài khoản `0x90F7…` rồi kết nối lại dashboard. **Không có** huy hiệu SUPERVISOR, và nút Mở khóa bị mờ.

**Không đưa tay sát dưới 3 cm:** HC-SR04 có vùng mù, quá gần sẽ đo sai. Ngưỡng 5 cm là **thông số trình diễn**, không phải khoảng cách an toàn theo ISO/TS 15066.

**Màn "sửa log thì bị phát hiện"** (cửa sổ 5, sau vài phút có dữ liệu):
```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python scripts\tamper_demo.py --db backend\data\hrc_log_hw.sqlite3
```
**Vì sao:**
1. Script cố tình sửa một dòng log trong database (đổi DANGER thành SAFE để che sự cố).
2. Nó tính lại Merkle root và so với root **đã nằm trên blockchain**: lệch.
3. Chữ ký HMAC của ESP32 chỉ ra **đúng dòng bị sửa**.
4. Cuối cùng script tự khôi phục dòng đã sửa.

---

## PHẦN C. Kết thúc buổi demo

**Làm gì**, theo thứ tự:
1. `Ctrl+C` ở cửa sổ 4 (dashboard) → cửa sổ 3 (backend) → cửa sổ 2 (blockchain).
2. Tắt **nguồn 5V servo**, rút **USB** ESP32. Relay tự nhả.
3. Cửa sổ 1 (cmd Admin): `Ctrl+C` để tắt Mosquitto, rồi gõ:
   ```bat
   net start mosquitto
   ```

**Vì sao bước 3 quan trọng:** trả Mosquitto về chế độ chạy ngầm như cũ. Nếu quên, lần sau chạy **bản mô phỏng** sẽ không có broker, simulator và backend không kết nối được.

---

## Phụ lục 1. Đổi mạng Wi-Fi hoặc IP laptop thay đổi

**Làm gì:**
1. Laptop vào mạng mới, `ipconfig` lấy IPv4 mới (ví dụ `192.168.43.10`).
2. **Tắt nguồn 5V servo** (nạp code lúc servo chạy dễ lỗi sụt nguồn).
3. Tạo lại file bí mật và nạp lại firmware:
   ```bat
   cd /d D:\Blockchain\Report_CK
   .venv\Scripts\activate.bat
   python scripts\make_secrets_h.py --ssid "TenWifi" --password "MatKhau" --mqtt-host 192.168.43.10
   cd iot_code\firmware
   ..\..\.pio-venv\Scripts\pio.exe run -e esp32dev -t upload --upload-port COM9
   ```

**Vì sao:**
- Tên Wi-Fi, mật khẩu và IP laptop nằm **trong firmware**, nên đổi mạng là phải nạp lại.
- `make_secrets_h.py` tự lấy khóa bảo mật HMAC từ file `.env`, nên ESP32 và backend luôn dùng **cùng một khóa**.
- Luôn ghi rõ `--mqtt-host`: laptop có card mạng ảo VMware, script tự dò IP có thể lấy nhầm.

**✅ Đúng khi:** `[SUCCESS]`. Không mở Serial Monitor; làm tiếp từ B2.

## Phụ lục 2. Muốn xem ESP32 "nói" gì (Serial Monitor)

```bat
cd /d D:\Blockchain\Report_CK\iot_code\firmware
..\..\.pio-venv\Scripts\pio.exe device monitor --port COM9
```
- Gõ `s` rồi Enter để xem trạng thái, ví dụ `d=332mm zone=0 estop=0() link=1 ntp=1`.
- Thoát: `Ctrl+C`.

⚠️ **Mở Serial Monitor sẽ khởi động lại ESP32**, nên board mất kết nối khoảng 20 giây. **Đừng mở khi đang demo.** Và phải thoát nó **trước khi nạp code**, vì cổng COM9 chỉ một chương trình dùng được một lúc.

Ý nghĩa `estop=1(…)`, tức lý do relay đang ngắt:

| Lý do | Nghĩa là | Làm gì |
|---|---|---|
| `DANGER` | có vật dưới 5 cm | rút tay / dọn vật cản |
| `LOCKED` | bị khóa trên blockchain | Supervisor mở khóa (B8 bước 6) |
| `BUTTON` | nút E-Stop đang nhấn | xoay nút nhả ra. Vẫn báo thì xem bảng lỗi |
| `LINK_LOST` | chưa nối được Mosquitto | kiểm B1–B2 (hotspot, IP, Mosquitto) |
| `SENSOR` | HC-SR04 không phản hồi | kiểm dây TRIG G21, ECHO G22, 5V, cầu chia áp |
| `BOOT` | vừa khởi động, chờ SAFE đủ 1 giây | dọn trống phía trước cảm biến |

## Phụ lục 3. Lỗi nhóm đã gặp và cách xử lý

| Hiện tượng | Nguyên nhân thật đã gặp | Cách xử lý |
|---|---|---|
| Không thấy cổng COM của ESP32 (chỉ thấy cổng Bluetooth) | Thiếu driver CP210x | Cài driver Silicon Labs (A1) |
| Nạp code báo `Packet content transfer stopped` / `chip stopped responding` | Servo/relay làm sụt nguồn USB lúc ghi flash | Tắt nguồn servo, rút dây 5V relay khi nạp. Đã hạ tốc độ nạp xuống 115200 |
| Board lặp `flash read err, 988` | **Board 38 chân nhưng cắm theo sơ đồ 30 chân**: dây rơi vào chân flash | **Không cắm gì vào CMD, CLK, SD0–SD3.** Cắm theo **chữ in cạnh chân** (G21, G22…) |
| Còi kêu liên tục, `estop=1(BUTTON)` | Nút E-Stop đang ở trạng thái nhấn / đấu nhầm tiếp điểm NC | Xoay nút nhả ra; dùng khối tiếp điểm **NO** (xanh lá, số 13–14) |
| Servo không quay, `LINK_LOST` | Bản firmware có mạng mà chưa nối Mosquitto | Làm B1–B2. Muốn thử servo không cần mạng: nạp `-e esp32dev_bench` |
| Mosquitto chạy nhưng **không bao giờ** hiện `New client connected … hrc-esp32-1` | **Hotspot iPhone tự chuyển sang 5 GHz** sau khi tắt/bật lại (kiểm tra: `netsh wlan show interfaces`, dòng *Band*) | iPhone: Điểm truy cập cá nhân → bật **"Tối đa hóa tính tương thích"**; rút USB ESP32 cắm lại |
| ESP32 đã `New client connected`, backend `Robot 1 ONLINE` **không hiện**, dashboard **OFFLINE / Chờ telemetry** | **2 Mosquitto cùng chạy**: service Windows tự bật lại, backend vào nhầm service | cmd Admin: `net stop mosquitto`, `sc query mosquitto` phải `STOPPED`. Backend tự kết nối lại sau vài giây |
| Dashboard OFFLINE trong khoảng 20–30 giây đầu sau khi ESP32 có điện | ESP32 **chưa lấy được giờ chuẩn** (NTP); chưa có giờ thì không gửi dữ liệu | Chờ thêm; hotspot phải có 4G |
| Mosquitto báo `Only one usage of each socket address` | Đã có Mosquitto khác giữ cổng 1883 (service chưa tắt, hoặc còn cửa sổ Mosquitto cũ) | `net stop mosquitto` (cmd Admin), đóng cửa sổ Mosquitto cũ, chạy lại |
| Servo không quay, `BOOT` / WARNING | Có vật trong vùng 10 cm trước cảm biến (dây, servo, mép bàn) | Dọn trống; kê cảm biến cao lên, hướng thẳng ra khoảng trống |
| Dashboard báo *"Không tìm thấy MetaMask"* | Trình duyệt chưa cài MetaMask | Cài extension (A5), mở dashboard trên đúng trình duyệt đó |
| Nút Mở khóa mờ, ghi *"Kết nối ví để mở khóa"* | Chưa bấm Kết nối MetaMask, hoặc đang chọn tài khoản khác | Bấm **Kết nối MetaMask**, chọn `0x3C44…93BC` |
| MetaMask báo `nonce too high` | Blockchain vừa bật lại | MetaMask → Cài đặt → Nâng cao → Xóa dữ liệu thẻ hoạt động |
| Mở khóa xong robot lại bị khóa | Tay vẫn trong vùng nguy hiểm, AI ghi sự cố mới | Rút tay ra xa hơn 10 cm rồi mới mở khóa |
| Backend `LOẠI [REPLAY]` liên tục | Dùng database của bản mô phỏng | Luôn chạy backend với `--db backend\data\hrc_log_hw.sqlite3` |
