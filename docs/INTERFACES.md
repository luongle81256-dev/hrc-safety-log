# Interface đã chốt – HRC Safety Log

> Mọi module (firmware, simulator, backend, AI, contract, dashboard) **phải** tuân thủ file này.
> Đổi interface ⇒ ghi lý do, cập nhật file này và **mọi** module liên quan trong cùng 1 commit.

## 1. MQTT topic

| Topic | Chiều | QoS | Ghi chú |
|---|---|---|---|
| `hrc/{robot_id}/telemetry` | ESP32 → backend | 0 | PubSubClient chỉ publish được QoS 0; backend phát hiện mất gói qua khoảng trống `seq` |
| `hrc/{robot_id}/cmd` | backend → ESP32 | 1 | ESP32 subscribe QoS 1 |
| `hrc/{robot_id}/heartbeat` | ESP32 → backend | 0 | Heartbeat có ký HMAC + LWT báo mất kết nối (mục 5) |

`{robot_id}` là số nguyên thập phân, ví dụ `hrc/1/telemetry`.

## 2. Quy tắc HMAC chung (áp dụng cho mục 3, 4, 5)

1. Thuật toán: **HMAC-SHA256**. Khóa = `bytes.fromhex(HRC_HMAC_KEY_HEX)`, dài 32 byte. Khóa nằm trong `.env` (backend) và `secrets.h` (ESP32), không commit.
2. Chỉ ký **chuỗi canonical** ASCII. Thứ tự trường trong JSON không quan trọng.
3. Số nguyên viết dạng thập phân: không dấu `+`, không số 0 ở đầu (trừ chính số `0`), không khoảng trắng. Phân cách bằng `|` (0x7C). Không có xuống dòng ở cuối.
4. Trường `hmac` là hex **chữ thường**, 64 ký tự. Bên nhận so sánh bằng hàm constant-time (`hmac.compare_digest` trong Python, so sánh XOR-tích lũy trong C). Không dùng `==` / `strcmp`.
5. **Tách miền (domain separation):** 3 loại bản tin có định dạng canonical khác nhau nên chữ ký của loại này không dùng được cho loại khác:
   - Telemetry: 7 số, không có tiền tố.
   - Lệnh: có token chữ `LOCK`/`UNLOCK`.
   - Heartbeat: có tiền tố `hb`.

   Nhờ vậy 1 khóa dùng chung vẫn an toàn.
6. Bản tin sai HMAC bị **loại bỏ và ghi log cảnh báo**. Bản tin đó không được đưa vào Merkle và không đi tới AI.

## 3. Telemetry (ESP32 → backend)

```json
{ "robot_id": 1, "seq": 42, "ts_ms": 1790000000000, "distance_mm": 350,
  "v_robot_mmps": 150, "zone": 1, "estop": 0, "hmac": "<64 hex>" }
```

| Trường | Ý nghĩa |
|---|---|
| `robot_id` | Mã robot (0..2^32−1), phải có trong `[system].robot_ids` và trùng `{robot_id}` trên topic |
| `seq` | Tăng **ngặt** theo từng robot, không lặp, kể cả sau khi reset (mục 6). Backend từ chối `seq <= seq cuối` |
| `ts_ms` | **Unix epoch mili-giây** (ESP32 đồng bộ NTP khi khởi động, xem mục 6) |
| `distance_mm` | Khoảng cách cảm biến đo được (8190 = không thấy vật / ngoài tầm) |
| `v_robot_mmps` | Độ lớn tốc độ robot hướng về phía người |
| `zone` | 0 = SAFE, 1 = WARNING, 2 = DANGER (ESP32 tự tính SSM) |
| `estop` | 1 nếu relay E-Stop đang ngắt |

Chuỗi canonical: `robot_id|seq|ts_ms|distance_mm|v_robot_mmps|zone|estop`, ví dụ `1|42|1790000000000|350|150|1|0`.

**Miền giá trị:**
- `seq`, `ts_ms` (và `nonce` của lệnh) ∈ [0, 2^63−1]. Giới hạn này để lưu được vào SQLite `INTEGER`, vốn là số có dấu 64 bit.
- `distance_mm`, `v_robot_mmps` ∈ [0, 65535] (kiểu uint16 trên firmware).
- Payload tối đa **512 byte**.

**Parse chặt:** JSON phải có **đúng** 8 trường, không thiếu, không thừa. Bản tin bị loại nếu có trùng khóa, số thực, `true/false` thay cho số, `NaN`, hoặc **bất kỳ ký tự `\` nào**.

Lý do cấm `\`: không bản tin hợp lệ nào cần escape. ArduinoJson trên ESP32 **gộp khóa trùng** thay vì báo lỗi, nên nếu cho phép escape thì kẻ tấn công có thể viết khóa dạng `"cmd"` để lách bước đếm khóa. Nhờ cấm escape, mọi parser hiểu một payload giống hệt nhau (chống *parser differential*).

**Test vector HMAC** (khóa = byte `00 01 02 … 1f`), đã đối chiếu bằng .NET HMACSHA256:

| Chuỗi canonical | HMAC-SHA256 |
|---|---|
| `1|42|1790000000000|350|150|1|0` | `0544056a530339a4f2a92ea818bb779c49c2e506d580c1f68f29cc13ad985d07` |
| `1|LOCK|1790000000123` | `430facbad90a2666d5846882c8bca4e719dcf26c49a0966fd458f7a84a78fad4` |
| `hb|1|1790000000500|0|0|99c3bd15cba71f37` | `2f836d5d8aafc121930eff7f34322a583b56ca03e3454303c47a41491a34d8f8` |

**Backend loại bản tin với các mã lý do sau** (bảng `rejected`):

| Mã | Ý nghĩa |
|---|---|
| `TOPIC` | topic sai định dạng |
| `KIND` | loại bản tin backend không nhận |
| `ROBOT_UNKNOWN` | robot chưa đăng ký |
| `SIZE` | payload quá 512 byte |
| `JSON` | JSON hỏng |
| `SCHEMA` | sai trường hoặc sai kiểu |
| `ROBOT_MISMATCH` | robot_id trong payload khác topic |
| `HMAC` | sai chữ ký |
| `REPLAY` | seq cũ hoặc trùng |
| `FUTURE_TS` | ts_ms vượt giờ backend quá 60 s |

## 4. Lệnh (backend → ESP32)

```json
{ "cmd": "LOCK", "nonce": 1790000000123, "hmac": "<64 hex>" }
```

- `cmd` ∈ {`LOCK`, `UNLOCK`}, viết hoa.
- Chuỗi canonical: `robot_id|cmd|nonce`, ví dụ `1|LOCK|1790000000123`. `robot_id` lấy từ topic `hrc/{robot_id}/cmd`; ESP32 dùng `HRC_ROBOT_ID` của chính nó. Nhờ đó lệnh gửi cho robot 1 không phát lại được sang robot 2.
- `nonce` = thời điểm backend phát lệnh, tính bằng **Unix epoch ms**. Backend bảo đảm nonce tăng ngặt: nếu trùng mili-giây thì cộng 1.
- ESP32 **chỉ chấp nhận** lệnh khi thỏa đủ 3 điều kiện:
  1. HMAC đúng.
  2. `nonce > last_nonce`. `last_nonce` lưu trong NVS nên reset xong vẫn không phát lại được lệnh cũ.
  3. `|nonce − now_ms| ≤ 30 000` nếu đã đồng bộ NTP. Điều kiện này chống lệnh bị giữ lại rồi phát muộn.
- Nguyên tắc an toàn bất đối xứng:
  - `LOCK` luôn được ưu tiên.
  - `UNLOCK` **không** tự bật relay. Nó chỉ gỡ khóa từ xa. Robot chỉ chạy lại khi thêm cả điều kiện SSM tại chỗ là SAFE.
- Backend **chỉ** gửi `UNLOCK` sau khi thấy event `Unlocked` on-chain (do SUPERVISOR ký).

## 5. Heartbeat (ESP32 → backend)

```json
{ "robot_id": 1, "ts_ms": 1790000000500, "state": 0, "estop": 0,
  "cfg": "99c3bd15cba71f37", "hmac": "<64 hex>" }
```

- `state`: 0 = ACTIVE, 1 = LOCKED (trạng thái khóa **tại thiết bị**).
- `cfg` = `HRC_CONFIG_HASH` trong firmware. Backend so với hash config của chính nó và cảnh báo nếu lệch.
- Chuỗi canonical: `hb|robot_id|ts_ms|state|estop|cfg`, ví dụ `hb|1|1790000000500|0|0|99c3bd15cba71f37`.
- Backend từ chối heartbeat có `ts_ms <=` heartbeat trước.
- **LWT** (Last Will, đăng ký lúc ESP32 connect): topic `hrc/{robot_id}/heartbeat`, payload `{"robot_id":1,"online":0}`, QoS 0, retain = false. LWT **không ký** vì nội dung cố định từ lúc connect. Làm vậy vẫn chấp nhận được, vì LWT giả chỉ gây **báo động nhầm**, nghiêng về phía an toàn, chứ không thể che giấu sự cố.
- Backend coi thiết bị mất kết nối nếu nhận LWT **hoặc** quá `failsafe_timeout_ms` không có heartbeat hợp lệ.

## 6. Thời gian và số thứ tự trên ESP32

- **`ts_ms`:** khi khởi động, ESP32 đồng bộ NTP. Chưa đồng bộ được thì **không gửi telemetry**, vì bản ghi sai giờ sẽ làm hỏng hộp đen. **Fast lane vẫn chạy bình thường** vì SSM không cần giờ thực.
- **`seq` bền qua reset:** NVS lưu `seq_reserved`. Khi khởi động: `seq = seq_reserved`, rồi ghi `seq_reserved += 1000`. Khi `seq` chạm mốc thì lại đặt trước 1000 số tiếp theo. Sau reset `seq` có thể nhảy cóc (tối đa 1000) nhưng **không bao giờ lặp**. Cách này chỉ ghi flash khoảng 1 lần mỗi 100 giây ở tần số 10 Hz.
- `last_nonce` (mục 4) lưu NVS mỗi khi nhận lệnh hợp lệ. Lệnh rất hiếm nên không lo mòn flash.

## 6b. Fast lane (firmware và simulator phải giống nhau)

Mỗi chu kỳ telemetry:
1. `v_meas` = tốc độ đã áp dụng ở chu kỳ trước.
2. `zone = SSM(distance_đo, v_meas)`.
3. **Ngắt relay ngay trong chu kỳ đó** nếu có một trong các điều kiện: `zone = DANGER`, `v_meas > v_robot_limit_mmps`, robot đang LOCKED, hoặc mất MQTT quá `failsafe_timeout_ms`.
4. Relay đang ngắt chỉ đóng lại khi SAFE liên tục ít nhất `estop_reset_hold_ms` **và** robot không LOCKED.
5. Tốc độ cho chu kỳ tới:
   - 0 nếu relay đang ngắt.
   - `warning_speed_percent`% của tốc độ lệnh nếu WARNING.
   - Bằng tốc độ lệnh nếu SAFE.
6. Telemetry gửi đúng các giá trị `(distance, v_meas, zone, estop)` đã dùng để quyết định. Nhờ vậy backend tính lại zone sẽ khớp 100%.

Ngoài ra:
- **Khởi động an toàn:** relay ngắt lúc khởi động.
- Trạng thái `locked` lưu trong NVS, nên **reset không gỡ được khóa**.

Đặc tả sống của các quy tắc trên là [tests/test_simulator.py](../tests/test_simulator.py).

## 7. Leaf Merkle

```
leaf = keccak256(abi.encode(robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop))
```

- Cả 7 trường mã hóa kiểu `uint256`. Với `abi.encode` (không phải `encodePacked`), mọi `uintN` đều được đệm lên 32 byte nên kết quả giống nhau.
- Cây dùng **sorted-pair**: `parent = keccak256(min(a,b) ‖ max(a,b))`, tương thích `MerkleProof.verify` của OpenZeppelin.
- Thứ tự lá: theo `seq` tăng dần. Tầng có số node lẻ thì node cuối **được đẩy thẳng lên**, không nhân đôi. Proof bỏ qua tầng đó.
- Batch rỗng thì không anchor. Batch đóng khi đủ `batch_max_records` hoặc hết `batch_max_interval_ms`.
- Bản tham chiếu:
  - TypeScript: [contracts/test/helpers/merkle.ts](../contracts/test/helpers/merkle.ts).
  - Python (Phase 4): phải cho **cùng root và proof**, có test đối chiếu.

## 8. Smart contract `HRCSafetyLog` (dành cho backend và dashboard)

| Hàm | Ai gọi | Ghi chú |
|---|---|---|
| `anchorLog(robotId, batchId, merkleRoot, fromSeq, toSeq)` | DEVICE gắn với robot | `batchId = lastBatchId + 1`; `fromSeq > lastToSeq` |
| `reportIncident(robotId, evidenceHash, severity, operator) → incidentId` | DEVICE gắn với robot | severity 1..3; `operator = 0` nghĩa là không quy lỗi |
| `unlock(robotId)` | SUPERVISOR | chỉ khi LOCKED; xóa operator đang check-in |
| `checkIn(robotId)` / `checkOut(robotId)` | OPERATOR / operator hiện tại | backend dùng `getRobot(robotId).currentOperator` làm tham số `operator` khi báo sự cố |
| `recordAudit(robotId, batchId, passed, reportHash)` | AUDITOR | |
| `registerRobot(robotId, device)` / `setRobotDevice` / `resetViolations(operator)` | DEFAULT_ADMIN | |
| `verifyLeaf(robotId, batchId, leaf, proof) → bool` | ai cũng gọi được (view) | ⚠ nhận leaf thô nên có rủi ro second-preimage. Chỉ dùng với leaf tự tính |
| `verifyRecord(batchId, record, proof) → bool` | ai cũng gọi được (view) | **Dùng cái này để kiểm chứng log**: tự tính leaf, kiểm tra seq nằm trong dải |
| `leafHash(record) → bytes32` | view | để đối chiếu cách tính leaf off-chain |
| `getRobot` / `robotState` / `getBatch` / `getIncident` / `violationCount` | view | |

`record` = struct `LogRecord(robotId, seq, tsMs, distanceMm, vRobotMmps, zone, estop)`, tất cả kiểu `uint256`.

Các event backend và dashboard cần nghe: `LogAnchored`, `EmergencyStop`, `Unlocked`, `OperatorBlocked`, `ViolationRecorded`, `OperatorCheckedIn/Out`, `AuditRecorded`, `RobotRegistered`, `RobotDeviceChanged`.

## 8b. AI → backend (`hrc_ai.inference.HrcDetector`)

`HrcDetector.update(telemetry) -> Prediction | None`. Hàm trả về `None` cho tới khi đủ cửa sổ 20 bản ghi. Nếu `seq` nhảy quá 4 thì cửa sổ bị xóa và phải chờ đủ lại.

| Trường `Prediction` | Ý nghĩa |
|---|---|
| `label`, `confidence`, `proba` | nhãn RandomForest ∈ {NORMAL, INTRUSION, OVERSPEED, SENSOR_FAULT} và xác suất |
| `if_score`/`if_anomaly`, `ae_error`/`ae_anomaly` | điểm và cờ bất thường của IsolationForest / Autoencoder |
| `anomaly` | `if_anomaly or ae_anomaly or label != NORMAL` |
| `danger_confirmed` | `zone == DANGER and label == INTRUSION`. **Điều kiện để gọi `reportIncident`** (Phase 4) |

## 8c. Backend → dashboard (`backend/hrc_backend/api.py`)

- **REST:**
  - `GET /api/config`: chainId, contract, ABI, RPC để đọc, explorer, `start_block`.
  - `GET /api/state`
  - `GET /api/telemetry/{robot}`
  - `GET /api/batches`
  - `GET /api/incidents`
  - `POST /api/verify/{robot}/{batch}`
- **WebSocket `/ws`:** bản tin đầu tiên là `{"type":"snapshot","state","history"}`. Sau đó là luồng `telemetry` (kèm `s_mm`, `warn_mm`, `ai`), `device`, `incident` (PENDING/SENT/FAILED), `batch`, `chain_event`, `command`.
- Dashboard **đọc trạng thái khóa và lịch sử sự cố thẳng từ contract**. WebSocket chỉ dùng để hiển thị real-time và báo "có event mới, hãy đọc lại chuỗi".

## 9. SSM

Công thức và toàn bộ tham số nằm trong [`config/hrc_config.toml`](../config/hrc_config.toml).
- Bản Python: [`hrc_common/ssm.py`](../hrc_common/ssm.py).
- Bản C: sinh tự động vào `iot_code/firmware/include/hrc_params.h`.

Hai bản phải cho kết quả **giống hệt nhau** (đã kiểm chứng: lệch 0 trên 71.643 điểm).

## Nhật ký quyết định

| Ngày | Quyết định |
|---|---|
| 2026-09-24 | Chốt HMAC cho lệnh (`robot_id|cmd|nonce`, nonce = Unix ms) và heartbeat (tiền tố `hb`, có ký, LWT không ký) |
| 2026-09-24 | Giữ `verifyLeaf` như interface gốc, **thêm** `verifyRecord` + `leafHash` để chống second-preimage |
| 2026-09-24 | Thêm vào contract: định danh robot ↔ ví DEVICE, check-in operator, ân xá có kiểm soát, recordAudit (chỉ bổ sung, không sửa interface cũ) |
| 2026-09-24 | Board đích: **ESP32 DevKit V1** (ESP32-WROOM-32). Nhóm 01 |
| 2026-09-24 | `seq`/`ts_ms`/`nonce` giới hạn 2^63−1 (SQLite). Thêm `[fastlane]`, `[security]`, `seq_reserve_block` vào config |
| 2026-09-24 | AI: cửa sổ 20 bản ghi, 20 đặc trưng; DANGER chỉ thành sự cố on-chain khi RF xác nhận INTRUSION; drift bị loại khỏi huấn luyện RF (không quan sát được) |
| 2026-09-25 | Edge AI: autoencoder INT8 (TFLite full-integer, per-channel) chạy bằng runtime C++ tự viết theo đặc tả TFLite thay cho thư viện TFLite Micro (esp-tflite-micro chỉ có cho ESP-IDF); kiểm chứng trùng từng bit với TFLite interpreter. Chỉ cảnh báo sớm, không nằm trong vòng an toàn |
| 2026-09-25 | Cấm ký tự `\` trong mọi payload JSON (phát hiện khi đối chiếu firmware ↔ Python: ArduinoJson gộp khóa trùng). Firmware đếm khóa trong payload thô |
| 2026-09-25 | `evidenceHash` của reportIncident = Merkle root (cùng định dạng leaf) của 20 bản ghi trong cửa sổ AI dùng để quyết định. LOCK/UNLOCK chỉ gửi theo event on-chain + đối chiếu heartbeat để gửi lại khi lệch. Thêm `[incident]`, `[chain].listener_poll_ms/tx_timeout_s` vào config; model AI kiểm tra `feature_fingerprint` (chỉ phần config ảnh hưởng đặc trưng) thay vì hash cả file |
