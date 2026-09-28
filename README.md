# HRC Safety Log – Hộp đen an toàn và định danh cho robot hợp tác

Đồ án cuối kỳ môn **Cơ sở Blockchain và Ứng dụng**, Đề tài 2, **Nhóm 01**. GVHD: Huỳnh Thế Thiện.

Hệ thống CPS 3 tầng **IoT → AI → Blockchain** cho không gian làm việc chung giữa người và cobot:
- ESP32 tự dừng robot khi người vào vùng nguy hiểm, theo luật Speed & Separation Monitoring (ISO/TS 15066).
- AI phân biệt **người thật** với **cảm biến báo sai**.
- Smart contract lưu **bằng chứng bất biến** (Merkle root của log), khóa robot khi có sự cố và khóa quyền operator vi phạm. **Chỉ SUPERVISOR** ký mới mở khóa được.

![Dashboard](docs/screenshots/dashboard_locked.png)

## Kiến trúc: 2 luồng

```
                        FAST LANE – an toàn, mili-giây, KHÔNG cần mạng
  VL53L0X ─► ESP32: SSM (ISO/TS 15066) ─► DANGER? ─► NGẮT RELAY (servo mất nguồn)
                │   Edge AI INT8 (cảnh báo sớm, không nằm trong vòng an toàn)
                │ MQTT + HMAC-SHA256 (telemetry 10 Hz, heartbeat có ký)
                ▼       TRUST LANE – truy vết, cỡ giây
  Mosquitto ─► Backend Python ─► kiểm HMAC / chống replay ─► SQLite (log off-chain)
                │   ├─ AI: RandomForest + IsolationForest + Autoencoder
                │   │     └─ DANGER được xác nhận là người thật ─► reportIncident() ─► robot LOCKED
                │   └─ gom batch ─► Merkle root ─► anchorLog()
                ◄─ lệnh LOCK/UNLOCK (ký HMAC) ◄─ nghe event EmergencyStop / Unlocked ◄─┘
  Dashboard (React + ethers + MetaMask): đọc chuỗi trực tiếp, SUPERVISOR ký unlock(), link Etherscan
```

**Blockchain không nằm trong vòng điều khiển an toàn.** Mạng hoặc chuỗi chậm, thậm chí sập, thì robot vẫn dừng kịp. Chuỗi đóng 2 vai:
- **Hộp đen:** log bị sửa là phát hiện được.
- **Trọng tài:** phân quyền, khóa/mở robot, và lưu lại ai chịu trách nhiệm.

## Đáp ứng yêu cầu đồ án

| Yêu cầu | Đáp ứng | Bằng chứng |
|---|---|---|
| End-to-end IoT → AI → Smart Contract | ✅ | `scripts/e2e_local.py`: 35/35 kiểm tra |
| Smart contract Solidity, deploy local + testnet | ✅ local · 🟡 Sepolia | Hardhat 3 + Ignition; `scripts/deploy_sepolia.py` đã chạy thử trọn đường Sepolia |
| Kết nối AI với contract bằng web3.py / ethers.js | ✅ | backend dùng web3.py v7, dashboard dùng ethers v6 |
| Báo cáo PDF + demo | 🟡 | `Report_Nhom01.pdf` (nhóm hoàn thiện), `scripts/demo_scenario.py` |
| **Sáng tạo: Edge AI INT8 trên ESP32** | ✅ (chờ board để đo thời gian) | F1 INT8 0,9661 so với float 0,9662; runtime firmware **trùng từng bit** TFLite |
| **Sáng tạo: Dashboard + MetaMask + Etherscan** | ✅ | mở khóa từ UI, kiểm bằng Playwright |
| **Sáng tạo: phần cứng thật** | 🟡 firmware xong, chờ board | build OK, logic C++ **khớp 100%** simulator |

## Kết quả chính

| Hạng mục | Số liệu |
|---|---|
| Smart contract | 50/50 test (45 mocha + 5 fuzz × 256 lần), **coverage 100%** dòng/statement, bytecode 7,1 KB |
| Gas `anchorLog` (batch 256 bản ghi) | 84.540 gas. **1 ca 8 giờ: 1.125 tx ≈ 0,095 ETH**; ghi từng dòng tốn ≈ 24,35 ETH (@1 gwei) |
| AI xác nhận DANGER | **precision 1,000 / recall 0,996**, 0 sự cố giả (luật đơn thuần: 693 sự cố giả) |
| AI phát hiện bất thường | Autoencoder F1 0,966, ROC-AUC 0,993 |
| Edge AI INT8 | file 6.952 B; trọng số giảm 2,3 lần; quyết định trùng float 99,93%; +5,5 KB flash |
| Độ trễ trust lane (DANGER → gửi LOCK, local) | 0,3–1,1 s. Fast lane ngắt relay tại ESP32 **trước đó** |
| Kiểm thử Python / firmware / UI | 198 pytest + 2 test giao diện; mutation test bắt được 100% lỗi cài vào |

Chi tiết từng tầng: [contracts](contracts/README.md) · [AI](ai_model/README.md) · [IoT + firmware](iot_code/README.md) · [backend](backend/README.md) · [dashboard](dashboard/README.md) · [interface đã chốt](docs/INTERFACES.md)

## Cấu trúc repo

| Thư mục | Nội dung |
|---|---|
| [contracts/](contracts/) | `HRCSafetyLog.sol` (OpenZeppelin AccessControl + MerkleProof), test Hardhat, module Ignition |
| [ai_model/](ai_model/) | Sinh dataset, huấn luyện, đánh giá, lượng tử INT8; **model đã huấn luyện** ở `models/` |
| [iot_code/](iot_code/) | Firmware ESP32 (PlatformIO) + simulator Python (đặc tả sống của firmware) |
| [backend/](backend/) | Ingest MQTT, AI, Merkle batcher, web3.py, nghe event, API FastAPI + WebSocket |
| [dashboard/](dashboard/) | Vite + React + ethers + MetaMask |
| [scripts/](scripts/) | demo, tamper, đối soát, deploy Sepolia, e2e, sinh cấu hình |
| [config/hrc_config.toml](config/hrc_config.toml) | **Cấu hình dùng chung duy nhất** (SSM, chu kỳ, batch, chính sách sự cố…) |
| [hrc_common/](hrc_common/) | Giao thức (HMAC, parse chặt), Merkle, SSM – dùng chung Python |
| [tests/](tests/) | pytest cho toàn hệ thống (kể cả logic firmware C++ biên dịch bằng g++) |
| [docs/](docs/) | Interface đã chốt, ảnh chụp |
| `Report_Nhom01.pdf` | Báo cáo kỹ thuật |

## Cài đặt (Windows)

```powershell
winget install -e --id Git.Git
winget install -e --id Python.Python.3.11
winget install -e --id OpenJS.NodeJS.LTS               # Node ≥ 22 (Hardhat 3, Vite 8)
winget install -e --id EclipseFoundation.Mosquitto     # hoặc: docker compose up -d mosquitto

git clone <repo> ; cd <repo>
py -3.11 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,ai]"                             # ai = TensorFlow (~300 MB), chỉ cần khi huấn luyện lại
python -m playwright install chromium                  # chỉ cần cho test giao diện
cd contracts ; npm install ; cd ..
cd dashboard ; npm install ; cd ..
```

Linux/macOS: tương tự với `python3.11 -m venv .venv && source .venv/bin/activate`, và cài Mosquitto bằng trình quản lý gói.

## Cấu hình

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"    # -> dán vào HRC_HMAC_KEY_HEX
```

**Local:** điền `ADMIN/DEVICE/SUPERVISOR/OPERATOR/AUDITOR_PRIVATE_KEY` bằng khóa **công khai** Hardhat #0–#4, lấy từ output của `npx hardhat node`.

Tham số hệ thống nằm trong [config/hrc_config.toml](config/hrc_config.toml). Sửa xong phải chạy `python scripts/gen_config.py`. Lệnh này sinh header firmware và file tham số contract; nếu quên, test sẽ báo.

## Chạy demo trên máy (local)

> Hướng dẫn **chạy thủ công từng bước**, kèm kết quả mong đợi ở từng terminal và từng kịch bản: [docs/HUONG_DAN_CHAY.md](docs/HUONG_DAN_CHAY.md)

Mở 4 terminal (đã kích hoạt `.venv`):

```powershell
# 1) Blockchain local (giữ chạy)
cd contracts ; npx hardhat node
# 2) Deploy contract (mỗi lần khởi động lại node), rồi chạy backend ở cùng terminal
cd contracts ; npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset ; cd ..
hrc-backend --batch-interval-ms 8000       # ingest + AI + blockchain + API/WebSocket (cổng 8000)
# 3) Dashboard: http://localhost:5173 -> "Kết nối MetaMask" (ví SUPERVISOR = Hardhat #2, xem dashboard/README)
cd dashboard ; npm run dev
# 4) Kịch bản demo có lời dẫn (hoặc tự chạy: hrc-sim --scenario approach)
python scripts/demo_scenario.py            # thêm --pause để dừng giữa các bước, --auto-unlock nếu không dùng MetaMask
```

Kịch bản demo gồm 5 bước:
1. Robot chạy bình thường.
2. Người vào vùng nguy hiểm: **relay ngắt ngay**, AI xác nhận, sự cố được ghi lên chuỗi, robot **LOCKED**.
3. Thử cho chạy lại thì contract **từ chối**.
4. SUPERVISOR **ký mở khóa bằng MetaMask**.
5. **Sửa 1 dòng log** thì đối soát **phát hiện ngay** và chỉ đúng dòng bị sửa.

Công cụ riêng lẻ:

```powershell
python scripts/tamper_demo.py --record-audit   # màn "sửa log -> bị phát hiện", auditor ghi kết quả lên chuỗi
python scripts/verify_log.py                   # đối soát toàn bộ log với Merkle root on-chain
hrc-cmd LOCK ; hrc-cmd UNLOCK                  # gửi lệnh đã ký bằng tay (thử firmware/simulator)
```

## Chạy trên Sepolia

> Hướng dẫn chi tiết từng bước (tạo ví, faucet, Alchemy, Etherscan, deploy, xử lý lỗi): [docs/DEPLOY_SEPOLIA.md](docs/DEPLOY_SEPOLIA.md)

1. **Chuẩn bị:**
   - RPC Sepolia miễn phí (Alchemy/Infura).
   - Ví MetaMask **chỉ dùng để test**, có khoảng 0,05 SepoliaETH lấy từ faucet.
   - (Tùy chọn) API key Etherscan.
2. **Cấu hình:** `Copy-Item .env.sepolia.example .env.sepolia`, rồi điền `SEPOLIA_RPC_URL`, `SEPOLIA_PRIVATE_KEY`, và `SUPERVISOR_ADDRESS` (địa chỉ MetaMask dùng để bấm Mở khóa). **Không commit, không gửi private key cho ai.**
3. **Deploy:**
   ```powershell
   python scripts/deploy_sepolia.py --dry-run-local   # (tùy chọn) chạy thử trên node local trước
   python scripts/deploy_sepolia.py --verify          # deploy thật + verify mã nguồn trên Etherscan
   ```
   Script kiểm số dư, deploy qua Ignition, kiểm vai trò trên chuỗi, rồi ghi `CONTRACT_ADDRESS` và `CHAIN_START_BLOCK` vào `.env.sepolia`.
4. **Chạy:** mọi lệnh như bản local, nhưng đặt hồ sơ mạng trước:
   ```powershell
   $env:HRC_NETWORK = "sepolia"
   hrc-backend
   ```
   Backend dùng DB riêng cho Sepolia. Dashboard tự hiện link **Etherscan** cho mọi giao dịch.

Trên Sepolia, mỗi khối mất khoảng 12 s, nên sự cố xuất hiện trên chuỗi sau khoảng 15–30 s. Fast lane vẫn ngắt relay tức thì. Mỗi batch tốn khoảng 85k gas; ở chu kỳ batch 30 s, 10 phút demo tốn khoảng 0,005 ETH.

## Kiểm thử

```powershell
python -m pytest                    # 198 test: giao thức, ingest, simulator, AI, Merkle, backend, API, firmware C++ (g++)…
python -m pytest -m ui              # 2 test giao diện dashboard (Playwright) – chạy RIÊNG
python scripts/e2e_local.py         # nghiệm thu end-to-end: 35 kiểm tra (cần hardhat node + Mosquitto)
cd contracts ; npx hardhat test --gas-stats --coverage
cd iot_code/firmware ; ..\..\.pio-venv\Scripts\pio.exe run     # build firmware ESP32
```

Test cần Hardhat node, broker, g++ hoặc TensorFlow sẽ **tự bỏ qua** (có ghi lý do) khi thiếu điều kiện. **Không chạy test MQTT hoặc e2e song song với một demo khác**, vì tất cả dùng chung topic `hrc/1/*`.

## Xử lý lỗi thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `Không có contract ở 0x… (node đã khởi động lại?)` | Hardhat node mất trạng thái khi khởi động lại. Deploy lại: `ignition deploy ... --network localhost --reset` |
| Backend log `DB … thuộc contract khác – đã cất sang …` | Bình thường sau khi deploy lại. DB cũ được **cất** sang file khác, không bị xóa |
| Simulator/backend báo `HRC_HMAC_KEY_HEX phải là 64 ký tự hex` | Chưa điền khóa HMAC trong `.env` |
| Mọi telemetry bị loại `REPLAY` | DB còn `seq` cao hơn NVS giả lập (`iot_code/simulator/.state/`). Xóa file NVS hoặc dùng DB mới |
| MetaMask báo `nonce too high` | Chuỗi local đã reset. MetaMask → Settings → Advanced → *Clear activity tab data* |
| Mở khóa xong robot lại bị khóa | Người vẫn ở vùng nguy hiểm, nên AI ghi sự cố mới. Chỉ mở khóa khi vùng SAFE |
| `HHE10723 Requested account index…` khi deploy Sepolia | Dùng `scripts/deploy_sepolia.py` (tự sinh file tham số vai trò), không gọi Ignition trực tiếp |
| ESP32 không kết nối được MQTT | Service Mosquitto mặc định chỉ nghe `localhost`. Xem [iot_code/README](iot_code/README.md) (nghe LAN + mở tường lửa cổng 1883) |
| `tf.lite.Interpreter is deprecated` | Cảnh báo của TF 2.21, không ảnh hưởng. Chỉ phần đánh giá INT8 dùng |

## Giới hạn và hướng phát triển

- **Dữ liệu AI là mô phỏng.** Simulator chạy đúng logic firmware. Khi có board, cần ghi dữ liệu thật, huấn luyện lại, và đo lại các tham số SSM đang giả định (thời gian phản ứng, thời gian dừng).
- **Drift chậm của 1 cảm biến ToF không phát hiện được.** Cần cảm biến dư (kênh kép, ISO 13849).
- **Oracle problem:** chuỗi chỉ đảm bảo log *không bị sửa sau khi ghi*, không đảm bảo cảm biến nói thật. Hướng nâng cấp: chip bảo mật hoặc TEE giữ khóa thiết bị, xác thực dữ liệu tại nguồn.
- **An toàn chức năng:** đây là mô hình học tập, **chưa được chứng nhận an toàn**. Relay và ESP32 không phải thiết bị an toàn đạt chuẩn.
- **API backend chưa có xác thực**, chỉ nghe `127.0.0.1`. Hướng: SIWE + HTTPS. Mở khóa có thể nâng thành multisig 2-of-N.

## Nhóm 01

| Thành viên | Vai trò |
|---|---|
| … | IoT & firmware |
| … | AI |
| … | Smart contract |
| … | Backend tích hợp, trưởng nhóm |
| … | Dashboard, báo cáo, slide |
