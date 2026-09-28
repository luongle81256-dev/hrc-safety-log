# Hướng dẫn chạy thủ công HRC Safety Log (từng bước)

Tài liệu này hướng dẫn **tự tay** bật từng thành phần, gõ từng lệnh và nhìn kết quả ở từng tầng (IoT → AI → Blockchain → Dashboard). Chạy theo cách này, nhóm hiểu và giải thích được hệ thống khi bảo vệ.

Các dòng log trích dưới đây được chụp từ lần chạy thật trên máy của nhóm. Số `seq`, địa chỉ, tx hash và giờ sẽ khác ở mỗi lần chạy.

> Muốn chạy nhanh, tự động hết thì xem [README](../README.md#chạy-demo-trên-máy-local) (`scripts/demo_scenario.py`).

---

## 0. Bức tranh tổng thể: mở 5 cửa sổ terminal

| Terminal | Vai trò trong hệ thống | Lệnh chính | Để chạy suốt? |
|---|---|---|---|
| **T1** | Blockchain local (thay cho Ethereum) | `npx hardhat node` | ✅ |
| **T2** | Deploy contract, rồi chạy **backend** (ingest + AI + ghi chuỗi + API) | `ignition deploy …` rồi `hrc-backend` | ✅ |
| **T3** | **Dashboard** web | `npm run dev` | ✅ |
| **T4** | **"Robot"**: simulator ESP32 (hoặc board thật) | `hrc-sim --scenario …` | chạy từng kịch bản |
| T5 (tùy chọn) | "Nghe lén" MQTT để xem bản tin thô | `mosquitto_sub …` | tùy |

Broker MQTT (Mosquitto) là **Windows service**, tự chạy nền nên không cần terminal riêng.

```
T4 hrc-sim ──MQTT(HMAC)──► Mosquitto ──► T2 hrc-backend ──web3.py──► T1 hardhat node
      ▲                                    │  (AI, Merkle, SQLite)          ▲
      └──── lệnh LOCK/UNLOCK có ký ◄───────┘                                │
                                  T3 dashboard (trình duyệt + MetaMask) ────┘ đọc chuỗi trực tiếp
```

---

## 1. Chuẩn bị (chỉ làm 1 lần)

### 1.1. Cho phép PowerShell chạy script

Windows mặc định **chặn** file `.ps1`. Hai lệnh `Activate.ps1` và `npx` sẽ báo lỗi *"running scripts is disabled on this system"* nếu chưa mở quyền. Chạy 1 lần (không cần Admin):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 1.2. Kiểm tra phần mềm đã cài

```powershell
node -v          # v22 trở lên (máy nhóm: v24.x)
git --version
py -3.11 -V      # Python 3.11.x
Get-Service mosquitto   # Status phải là Running
```

Thiếu thứ gì thì cài theo mục **Cài đặt** trong [README](../README.md#cài-đặt-windows).

### 1.3. Cài thư viện cho dự án

```powershell
cd D:\Blockchain\Report_CK
py -3.11 -m venv .venv                 # bỏ qua nếu đã có thư mục .venv
.\.venv\Scripts\Activate.ps1           # đầu dòng lệnh hiện (.venv) là đúng
pip install -e ".[dev]"                # thêm ,ai nếu muốn huấn luyện lại AI (TensorFlow ~300 MB)
cd contracts ; npm install ; cd ..
cd dashboard ; npm install ; cd ..
```

### 1.4. Tạo file `.env`

```powershell
Copy-Item .env.example .env            # bỏ qua nếu đã có .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Mở `.env` rồi điền:
- `HRC_HMAC_KEY_HEX` = chuỗi 64 ký tự vừa sinh. Đây là khóa bí mật chung giữa robot và backend.
- `ADMIN_PRIVATE_KEY`, `DEVICE_PRIVATE_KEY`, `SUPERVISOR_PRIVATE_KEY`, `OPERATOR_PRIVATE_KEY`, `AUDITOR_PRIVATE_KEY` = private key của **Account #0, #1, #2, #3, #4**. Lấy từ màn hình T1 khi chạy `npx hardhat node` (mục 2.2).

> Các key Hardhat là khóa **công khai, ai cũng biết**, chỉ dùng cho chuỗi local. **Tuyệt đối không** nạp tiền thật vào các địa chỉ này và không dùng chúng trên Sepolia.

Máy của nhóm đã có `.env` điền đầy đủ, nên có thể bỏ qua bước này.

### 1.5. Cài MetaMask (để bấm Mở khóa trên dashboard)

1. Cài extension MetaMask cho Chrome/Edge và tạo một ví **chỉ dùng để demo**.
2. *Add account → Import account* → dán private key **Account #2** (SUPERVISOR). Địa chỉ phải là `0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC`.
3. Mạng Hardhat local (chainId 31337, RPC `http://127.0.0.1:8545`): dashboard sẽ đề nghị thêm mạng khi kết nối lần đầu.

---

## 2. Khởi động hệ thống (mỗi lần demo)

Ở **mọi** terminal, trước hết chạy:

```powershell
cd D:\Blockchain\Report_CK
.\.venv\Scripts\Activate.ps1
```

### 2.1. Kiểm tra broker MQTT

```powershell
Get-Service mosquitto
```

Cột `Status` phải là `Running`. Nếu là `Stopped`, mở PowerShell **Admin** và chạy `Start-Service mosquitto`.

### 2.2. T1: bật blockchain local

```powershell
cd contracts
npx hardhat node
```

Kết quả mong đợi:

```
Started HTTP and WebSocket JSON-RPC server at http://127.0.0.1:8545/

Accounts
========
Account #0: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 (10000 ETH)
Private Key: 0xac09…
Account #1: 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 (10000 ETH)
...
```

**Để yên cửa sổ này.** Mỗi giao dịch sau này sẽ hiện ở đây với dòng `eth_sendRawTransaction`.

> Báo lỗi `EADDRINUSE 127.0.0.1:8545` nghĩa là **đã có một node đang chạy**, có thể ở cửa sổ khác hoặc bị treo từ lần trước. Dùng luôn node đó, hoặc tắt nó rồi chạy lại:
> ```powershell
> Get-NetTCPConnection -LocalPort 8545 -State Listen | % { Stop-Process -Id $_.OwningProcess }
> ```

### 2.3. T2: deploy smart contract

```powershell
cd contracts
npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset
cd ..
```

Kết quả mong đợi:

```
Batch #2
  Executed HRCSafetyLogModule#grantAuditor
  Executed HRCSafetyLogModule#grantDevice
  Executed HRCSafetyLogModule#grantOperator
  Executed HRCSafetyLogModule#grantSupervisor
  Executed HRCSafetyLogModule#registerRobot_1

[ HRCSafetyLogModule ] successfully deployed 🚀

Deployed Addresses
HRCSafetyLogModule#HRCSafetyLog - 0xcbEAF3BD…
```

Lệnh này làm 3 việc: deploy contract, **cấp 4 vai trò** (DEVICE, SUPERVISOR, OPERATOR, AUDITOR), rồi **đăng ký robot 1** gắn với ví DEVICE. Backend tự đọc địa chỉ contract từ thư mục `contracts/ignition/deployments/`, nên không cần chép địa chỉ vào đâu.

> **Mỗi lần tắt và bật lại T1 thì phải deploy lại**, vì Hardhat node không lưu dữ liệu. `--reset` nghĩa là deploy mới hoàn toàn.

### 2.4. T2 (tiếp): chạy backend

```powershell
hrc-backend --batch-interval-ms 8000
```

`--batch-interval-ms 8000` gom log **8 giây một batch** để demo thấy nhanh. Mặc định là 30 giây.

Kết quả mong đợi:

```
hrc-backend: Mạng local (chain 31337), contract 0xcbEAF3BD…, quét event từ block 204
hrc-backend: API dashboard: http://127.0.0.1:8000  (WebSocket /ws)
hrc-backend: Đã kết nối broker localhost:1883
hrc-listener: Listener bắt đầu từ block 209 (bỏ qua lịch sử)
```

Nếu thấy `DB … thuộc contract khác – đã cất sang …` thì đó là **bình thường** sau khi deploy lại. Log cũ được cất sang file khác chứ không bị xóa.

### 2.5. T3: dashboard

```powershell
cd dashboard
npm run dev
```

Mở trình duyệt vào **http://localhost:5173**, rồi làm tiếp:
1. Bấm **Kết nối MetaMask** và chọn tài khoản #2.
2. Góc trên phải phải hiện huy hiệu **SUPERVISOR**.
3. Lúc này chưa có robot nào gửi dữ liệu, nên thiết bị hiện *offline*. Đây là bình thường.

### 2.6. T5 (tùy chọn): nghe bản tin MQTT thô

```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t "hrc/1/#" -v
```

Cửa sổ này cho thấy robot **thực sự gửi gì** lên mạng, nên hợp để trình chiếu khi giải thích HMAC:

```
hrc/1/telemetry {"robot_id":1,"seq":2,"ts_ms":1790322683334,"distance_mm":935,"v_robot_mmps":0,"zone":0,"estop":1,"hmac":"91a71b02…"}
hrc/1/heartbeat {"robot_id":1,"ts_ms":1790322683334,"state":0,"estop":1,"cfg":"40037d15ebf0e786","hmac":"2e28f4ce…"}
```

---

## 3. Chạy từng kịch bản (T4 là "robot")

Mỗi lệnh `hrc-sim` chạy như **một con ESP32 thật**: đo khoảng cách, tự tính vùng an toàn, tự ngắt relay, ký HMAC, gửi 10 bản tin/giây và nghe lệnh LOCK/UNLOCK.

Trên màn hình simulator, mỗi dòng log có dạng:

```
seq=1071  d= 143mm v= 67mm/s DANGER  estop=1(DANGER) | INTRUSION
 │          │        │         │       │                 └ nhãn đúng (để đối chiếu với AI)
 │          │        │         │       └ relay: 1 = NGẮT (kèm lý do), 0 = robot đang chạy
 │          │        │         └ vùng SSM do "ESP32" tự tính
 │          │        └ tốc độ robot
 │          └ khoảng cách người – robot
 └ số thứ tự bản tin (chống phát lại)
```

### Kịch bản A: robot làm việc bình thường

```powershell
hrc-sim --scenario normal --duration 30
```

| Nơi xem | Điều cần thấy |
|---|---|
| T4 (sim) | Mấy giây đầu `estop=1(BOOT)`: vừa khởi động nên giữ relay ngắt cho đến khi chắc chắn vùng SAFE. Sau đó là `SAFE estop=0` |
| T2 (backend) | `Robot 1 ONLINE`, và cứ ~8 s một dòng `NEO batch robot 1 #1: 82 bản ghi (seq 2..83) root=0x4f4f… gas=101478 tx=0x96e4…` |
| T1 (node) | Mỗi batch có một giao dịch `anchorLog` |
| Dashboard | Zone **SAFE** màu xanh, biểu đồ khoảng cách chạy, bảng **Batch Merkle** tăng dần |

**Ý nghĩa:** log thô nằm trong SQLite (rẻ). Trên chuỗi chỉ lưu **1 Merkle root 32 byte cho mỗi batch**.

Khi sim dừng, backend báo `Robot 1 MẤT KẾT NỐI (quá 2000 ms không có heartbeat)`. Đây là bình thường: backend nhận ra thiết bị mất kết nối.

### Kịch bản B: người đi vào vùng nguy hiểm (màn chính của đồ án)

```powershell
hrc-sim --scenario approach --duration 20
```

Khoảng giây thứ 7, người đi tới sát robot:

**T4 (sim):**

```
seq=1071  d= 143mm v= 67mm/s DANGER  estop=1(DANGER) | INTRUSION     <- FAST LANE: tự ngắt relay NGAY
WARNING >>> NHẬN LỆNH HỢP LỆ – robot LOCKED                         <- lệnh LOCK từ backend (đã kiểm HMAC)
seq=1111  d= 366mm v=  0mm/s SAFE    estop=1(LOCKED) | NORMAL        <- người đi rồi nhưng VẪN KHÓA
```

**T2 (backend):**

```
hrc-incident: SỰ CỐ robot 1: INTRUSION (độ tin cậy 1.00, mức 2, bằng chứng seq 1052..1071)
hrc-backend: GHI SỰ CỐ on-chain #1 robot 1 (INTRUSION, mức 2, …) tx=0x87da… gas=122235
hrc-backend: >>> Gửi LOCK tới robot 1 (nonce 1790322711013)
```

**Dashboard:** zone **DANGER** màu đỏ, trạng thái on-chain chuyển sang **LOCKED**, và bảng **Sự cố** có thêm 1 dòng.

**Thứ tự sự việc cần giải thích khi bảo vệ:**
1. ESP32 tự ngắt relay, **không chờ mạng hay blockchain** (fast lane).
2. AI ở backend xác nhận đây là **người thật**, không phải cảm biến lỗi, rồi gọi `reportIncident()` lên chuỗi.
3. Contract chuyển robot sang **LOCKED** và phát event `EmergencyStop`.
4. Backend nghe event đó rồi gửi lệnh `LOCK` có ký HMAC xuống robot.
5. Robot lưu trạng thái khóa vào NVS, nên **rút điện cắm lại vẫn khóa**.

### Kịch bản C: thử cho robot chạy lại khi đang khóa

Chạy lại kịch bản `normal`: robot vẫn `estop=1(LOCKED)` dù không có ai ở gần.

```powershell
hrc-sim --scenario normal --duration 600      # để chạy nền trong lúc làm bước D
```

Thử để OPERATOR nhận ca (check-in) robot đang khóa. Mở thêm một terminal (đã kích hoạt `.venv`):

```powershell
python -c "from hrc_backend.chain import ChainClient; ChainClient.from_env('OPERATOR').check_in(1)"
```

Kết quả: **contract từ chối** bằng lỗi `ChainError`, vì robot đang LOCKED nên không ai nhận ca được.

### Kịch bản D: SUPERVISOR mở khóa

Giữ kịch bản `normal` ở bước C đang chạy, để vùng làm việc SAFE.

**Cách 1: trên dashboard bằng MetaMask** (đây là cách demo):
1. Bấm **Mở khóa (ký bằng MetaMask)**.
2. MetaMask hiện giao dịch `unlock` → bấm **Xác nhận**.

**Cách 2: bằng lệnh**, khi không có MetaMask:

```powershell
python -c "from hrc_backend.chain import ChainClient; print(ChainClient.from_env('SUPERVISOR').unlock(1).tx_hash)"
```

Kết quả mong đợi:

```
T2: hrc-backend: Event Unlocked: robot 1 được SUPERVISOR 0x3C44CdDd mở khóa
T2: hrc-backend: >>> Gửi UNLOCK tới robot 1 (nonce 1790322741456)
T4: WARNING >>> NHẬN LỆNH HỢP LỆ – robot đã gỡ khóa (chờ SAFE)
T4: seq=2071  d= 927mm v=142mm/s SAFE  estop=0 | NORMAL        <- robot chạy lại
```

Dashboard chuyển về **ACTIVE**, bảng sự cố có thêm thông tin *ai* đã mở khóa.

> **Không có đường nào mở khóa mà không để lại dấu vết trên chuỗi.** Backend chỉ gửi UNLOCK *sau khi* thấy event `Unlocked`. Lệnh Serial trên ESP32 cũng không gỡ được khóa.

Thử thêm: gửi lệnh bằng tay qua `hrc-cmd UNLOCK`. Lệnh này có ký nên robot sẽ nhận, nhưng **chuỗi không hề thay đổi** (dashboard vẫn LOCKED). Nó chỉ dùng để thử firmware; cơ chế chính thức luôn đi qua contract.

### Kịch bản E: cảm biến báo sai (AI chứng tỏ giá trị)

```powershell
hrc-sim --scenario sensor_noise --duration 25 --seed 7
```

- Cảm biến thỉnh thoảng **nhảy vọt** (ví dụ tụt xuống 74 mm, tức vùng DANGER). Relay vẫn ngắt, vì fast lane luôn chọn an toàn.
- AI nhận ra đây là **SENSOR_FAULT**, không phải người, nên **không ghi sự cố và robot không bị khóa**.
- Kiểm tra ở T2: dòng `Thống kê` có `'ai_SENSOR_FAULT': 1` nhưng `'incidents'` **không tăng**.

**Ý nghĩa:** nếu chỉ dùng luật khoảng cách, riêng trên tập test đã có **693 sự cố giả**, và mỗi lần như vậy phải gọi SUPERVISOR tới mở khóa. AI giảm con số này về **0**.

### Kịch bản F: tấn công giả mạo và phát lại bản tin

```powershell
hrc-sim --scenario normal --duration 6 --attack forge --attack-every 10    # sửa distance, giữ nguyên chữ ký cũ
hrc-sim --scenario normal --duration 6 --attack replay --attack-every 10   # phát lại bản tin cũ
```

Ở T2 sẽ thấy:

```
hrc-ingest: LOẠI [HMAC] topic=hrc/1/telemetry sai chữ ký seq=3011
```

Với `replay`, dòng log là `LOẠI [REPLAY]`. Bản tin bị loại **không vào log, không vào Merkle root**.

### Kịch bản G: robot vượt tốc độ

```powershell
hrc-sim --scenario overspeed --duration 30
```

Cứ khoảng 10 s, bộ điều khiển "lỗi" ra lệnh 300–450 mm/s. Khi đó vùng an toàn S **tự nới rộng**, vì robot nhanh thì cần khoảng cách lớn hơn (công thức ISO/TS 15066), và fast lane cắt relay nếu người ở gần. AI gắn nhãn `OVERSPEED`.

---

## 4. Kiểm toán log (vai AUDITOR)

Chạy được khi backend đang chạy hoặc đã tắt.

### 4.1. Đối soát toàn bộ log với chuỗi

```powershell
python scripts/verify_log.py
```

```
Đối soát robot 1 – contract 0xcbEAF3BD… (chain 31337), 11 batch
   batch  bản ghi  root      verifyRecord  kết quả
       5       26  khớp          3/3       ✓ nguyên vẹn
       ...
KẾT QUẢ: 11/11 batch nguyên vẹn
```

### 4.2. Sửa 1 dòng log thì bị phát hiện (màn thuyết phục nhất)

```powershell
python scripts/tamper_demo.py --record-audit
```

```
1) ĐỐI SOÁT TRƯỚC KHI SỬA      root off-chain = root on-chain  => KHỚP
2) SỬA LOG: seq 4037: distance 74 -> 774 mm, zone DANGER -> SAFE
3) ĐỐI SOÁT SAU KHI SỬA        => LỆCH !!!
   HMAC thiết bị không khớp ở seq: [4037]  <= dòng bị sửa
4) AUDITOR ghi lên chuỗi: recordAudit(batch 9, passed=false) – 0x247d…
5) Đã khôi phục dòng seq 4037 – đối soát lại: KHỚP
```

Script tự khôi phục dòng đã sửa ở bước 5. Thêm `--keep` nếu muốn giữ dòng sai để mở SQLite ra xem.

Trên dashboard, bảng **Batch Merkle** có nút **Đối soát** cho từng batch, cho cùng kết quả.

---

## 5. Xem dữ liệu trực tiếp (tùy chọn)

| Muốn xem | Cách |
|---|---|
| Toàn bộ API, thử từng endpoint | **http://127.0.0.1:8000/docs** (Swagger do FastAPI tự sinh) |
| Trạng thái robot | `Invoke-RestMethod http://127.0.0.1:8000/api/state \| ConvertTo-Json -Depth 6` |
| Danh sách batch / sự cố | `…/api/batches`, `…/api/incidents` |
| Database log | File `backend/data/hrc_log.sqlite3`, mở bằng *DB Browser for SQLite*. Bảng `telemetry`, `batches`, `incidents`, `rejected` |
| Giao dịch trên chuỗi | Cửa sổ T1, hoặc dashboard |

Trong `/api/state`, mỗi bản tin có thêm kết quả AI:

```json
"ai": {"label":"NORMAL","confidence":0.772,"anomaly":true,"danger_confirmed":false}
```

`danger_confirmed = true` là **điều kiện duy nhất** để backend gọi `reportIncident()`.

---

## 6. Tắt hệ thống và chạy lại lần sau

**Tắt:** bấm `Ctrl+C` lần lượt ở T4 → T3 → T2 → T1. Mosquitto là service nên cứ để chạy.

**Lần sau**, làm lại từ mục 2.2 và **nhớ deploy lại** (2.3), vì chuỗi local đã bị xóa.

Sau khi chuỗi local reset có hai hiện tượng cần xử lý:
- **MetaMask báo `nonce too high`:** vào MetaMask → *Settings → Advanced → Clear activity tab data*.
- **Backend báo `Robot 1 mất 880 bản tin (seq 122..1001)`:** đây là **bình thường**. Mỗi lần "khởi động", thiết bị nhảy `seq` lên mốc 1000 kế tiếp (thiết kế chống replay sau khi mất điện), nên backend ghi nhận một khoảng trống.

---

## 7. Chạy trên Sepolia (testnet thật)

1. **Chuẩn bị:**
   - RPC Sepolia miễn phí từ Alchemy hoặc Infura.
   - Ví **test** có khoảng 0,05 SepoliaETH, lấy từ faucet.
   - (Tùy chọn) API key Etherscan để verify mã nguồn.
2. **Cấu hình:** chạy `Copy-Item .env.sepolia.example .env.sepolia`, rồi điền `SEPOLIA_RPC_URL`, `SEPOLIA_PRIVATE_KEY`, `SUPERVISOR_ADDRESS` (địa chỉ MetaMask sẽ bấm Mở khóa) và `ETHERSCAN_API_KEY`. **Không gửi file này cho ai, không commit.**
3. **Deploy:**
   ```powershell
   python scripts/deploy_sepolia.py --verify
   ```
   Script in số dư và phí ước tính, **hỏi xác nhận** rồi mới tiêu ETH. Khi xong, nó tự ghi `CONTRACT_ADDRESS` vào `.env.sepolia`.
4. **Chạy:** ở **T2 và T4**, đặt biến mạng trước mọi lệnh:
   ```powershell
   $env:HRC_NETWORK = "sepolia"
   hrc-backend            # T2
   hrc-sim --scenario approach --duration 60   # T4
   ```
   Không cần T1 (Hardhat node) và không cần deploy local. Trong MetaMask, chuyển sang mạng **Sepolia**.
5. **Khác với local:**
   - Mỗi khối mất khoảng 12 s, nên sự cố hiện trên chuỗi sau **15–30 s**. Relay vẫn ngắt tức thì.
   - Dashboard có **link Etherscan** cho mọi giao dịch.
   - Batch nên để mặc định 30 s để tiết kiệm gas: không truyền `--batch-interval-ms`.

---

## 8. Chạy với board ESP32 thật

Thay T4 (`hrc-sim`) bằng board. Chi tiết đấu dây xem [iot_code/README](../iot_code/README.md#lắp-ráp-và-nạp-firmware-khi-có-phần-cứng).

1. Tạo file bí mật cho firmware. Khóa HMAC được lấy từ `.env`, nên luôn trùng với backend:
   ```powershell
   python scripts/make_secrets_h.py --ssid "TenWifi" --password "MatKhau"
   ```
2. Cho Mosquitto nghe trong mạng LAN (mặc định chỉ nghe `localhost`), chạy ở PowerShell **Admin**:
   ```powershell
   net stop mosquitto
   & "C:\Program Files\mosquitto\mosquitto.exe" -c infra\mosquitto\mosquitto.native.conf -v
   New-NetFirewallRule -DisplayName "MQTT 1883" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow
   ```
3. Nạp firmware và mở Serial:
   ```powershell
   cd iot_code\firmware
   ..\..\.pio-venv\Scripts\pio.exe run -t upload
   ..\..\.pio-venv\Scripts\pio.exe device monitor
   ```
   Serial phải hiện `[OK] Tự kiểm tra HMAC đạt`.
4. Từ đây làm y như mục 3, nhưng thay kịch bản bằng **tay thật**: đưa tay lại gần cảm biến. Lệnh Serial hữu ích:
   - `v 400`: ra lệnh vượt tốc.
   - `s`: xem trạng thái.
   - `stats`: xem độ trễ.
5. Đo độ trễ để đưa vào báo cáo:
   ```powershell
   pip install -e ".[hw]"
   python scripts/measure_latency.py --port COM5 --duration 60
   ```
   `COM5` là cổng của board; xem trong *Device Manager → Ports*.

---

## 9. Kiểm thử tự động (sau khi sửa code)

```powershell
python -m pytest                 # 198 test (khoảng vài phút)
python scripts/e2e_local.py      # 35 kiểm tra end-to-end (cần T1 + đã deploy, KHÔNG chạy cùng lúc với demo)
cd contracts ; npx hardhat test  # 50 test smart contract
```

> **Không** chạy pytest hoặc e2e **song song** với một demo đang mở, vì chúng dùng chung topic MQTT `hrc/1/*` và sẽ làm nhiễu nhau.

---

## 10. Checklist trước buổi bảo vệ

- [ ] `Get-Service mosquitto` hiện Running.
- [ ] T1 node chạy → đã deploy (thấy `successfully deployed`) → T2 backend báo `Đã kết nối broker`.
- [ ] Dashboard mở được, MetaMask kết nối đúng **Account #2** và có huy hiệu SUPERVISOR.
- [ ] Đã xóa *activity tab data* trong MetaMask nếu vừa khởi động lại node.
- [ ] Chạy thử 1 lượt: normal → approach (thấy LOCKED) → normal → bấm Mở khóa → `tamper_demo.py`.
- [ ] Robot **không** đang LOCKED từ lần thử trước. Nếu đang khóa, mở khóa rồi mới bắt đầu.
- [ ] Nếu demo Sepolia: kiểm số dư ví DEVICE còn ETH trả gas.

## 11. Lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| `running scripts is disabled on this system` | Chạy mục 1.1 |
| `hrc-backend` / `hrc-sim` không nhận là lệnh | Chưa kích hoạt venv: `.\.venv\Scripts\Activate.ps1` |
| `EADDRINUSE … 8545` | Đã có node chạy sẵn (xem mục 2.2) |
| `Không có contract ở 0x… (node đã khởi động lại?)` | Deploy lại (mục 2.3) |
| `HRC_HMAC_KEY_HEX phải là 64 ký tự hex` | Chưa điền khóa trong `.env` (mục 1.4) |
| Backend không có dòng nào khi chạy sim | Mosquitto chưa chạy, hoặc sim và backend dùng `.env` khác nhau |
| Mọi telemetry bị `LOẠI [REPLAY]` | DB còn `seq` cao hơn trạng thái sim. Xóa thư mục `iot_code/simulator/.state/` |
| Mở khóa xong robot lại bị khóa | Người vẫn ở vùng nguy hiểm nên AI ghi sự cố mới. Chỉ mở khóa khi vùng SAFE (chạy `--scenario normal`) |
| Nút Mở khóa bị mờ | MetaMask đang ở tài khoản khác #2. Chuyển tài khoản rồi kết nối lại |
| MetaMask `nonce too high` | *Settings → Advanced → Clear activity tab data* |
| Dashboard trắng, không có dữ liệu | T2 backend chưa chạy, hoặc chạy ở cổng khác 8000 |
