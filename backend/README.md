# /backend – Trust lane

Package `hrc_backend`, cài chung bằng `pip install -e ".[dev]"` ở gốc repo.

```
MQTT ──► ingest (HMAC, replay, schema) ──► SQLite ──► AI (HrcDetector) ──► chính sách sự cố ─┐
                                              │                                              ▼
                                              └──► batcher: Merkle root ──► [chain-writer: 1 luồng, hàng đợi ưu tiên]
                                                                             ├─ reportIncident (ưu tiên 0)
                                                                             └─ anchorLog      (ưu tiên 1)
chuỗi ──► [chain-listener] EmergencyStop ──► LOCK (ký HMAC) ──► MQTT ──► ESP32
                           Unlocked      ──► UNLOCK
heartbeat (có ký) lệch trạng thái với chuỗi ──► gửi lại lệnh (thiết bị offline lúc có lệnh vẫn tự đồng bộ)
```

| Module | Việc |
|---|---|
| `storage.py` | SQLite (WAL): `telemetry`, `rejected`, `seq_gaps`, `device_status`, `cmd_log`, `batches`, `incidents`, `kv` |
| `ingest.py` | `TelemetryIngestor`, `HeartbeatMonitor` (online, LWT, timeout) |
| `chain.py` | web3.py v7: ký tx cục bộ, **mô phỏng trước khi gửi** để giải mã custom error (ví dụ `SeqOverlap(...)`), đọc event |
| `batcher.py` | Gom batch → Merkle root → `anchorLog`. batchId/dải seq lấy **từ chuỗi**; ghi PENDING trước khi gửi → `recover()` khi khởi động lại |
| `incidents.py` | AI → `reportIncident`: INTRUSION (DANGER + AI xác nhận, quy lỗi operator), OVERSPEED/SENSOR_FAULT kéo dài (không quy lỗi người); không báo lặp khi đang LOCKED |
| `listener.py` | Poll event; lần đầu **bỏ qua lịch sử** (không phát lại lệnh cũ); lưu vị trí block |
| `verify.py` | Đối soát log với root on-chain + `verifyRecord`; khi lệch thì dùng HMAC thiết bị để chỉ ra dòng bị sửa |
| `service.py` | Lệnh `hrc-backend`: ghép tất cả |
| `ingest_service.py`, `send_cmd.py`, `ai.py` | `hrc-ingest` (chỉ ingest), `hrc-cmd`, `hrc-ai-selftest` |

## Chạy local

```powershell
# terminal 1
cd contracts; npx hardhat node
# terminal 2 (1 lần sau mỗi lần khởi động node)
cd contracts; npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset
# terminal 2
hrc-backend --batch-interval-ms 5000
# terminal 3
hrc-sim --scenario mixed
```

`.env` cần có `DEVICE_PRIVATE_KEY`. Ở local, đây là khóa **công khai** của Hardhat account #1, in ra khi chạy `npx hardhat node`. Script demo cần thêm `SUPERVISOR_PRIVATE_KEY` (#2) và `OPERATOR_PRIVATE_KEY` (#3).

## Quyết định thiết kế đáng chú ý

- **Một luồng ghi chuỗi duy nhất**: nonce tuần tự nên không bao giờ trùng. Sự cố được ưu tiên hơn batch.
- **Chuỗi là nguồn sự thật**: LOCK/UNLOCK chỉ được gửi khi có **event** on-chain. Unlock từ dashboard qua MetaMask (Phase 6) vì thế tự đến được thiết bị mà không cần gọi API backend.
- **Không tin trạng thái cục bộ**: batchId, dải seq và trạng thái khóa đều đọc lại từ contract. DB bị xóa giữa chừng thì bản ghi trùng dải đã neo được đánh dấu *orphan*, không làm kẹt hệ thống.
- **Merkle phát hiện, HMAC khoanh vùng**: root lệch chứng minh batch đã bị sửa (kể cả khi người sửa là chính quản trị DB). HMAC từng dòng chỉ ra dòng nào bị sửa, nếu kẻ sửa không có khóa thiết bị.
