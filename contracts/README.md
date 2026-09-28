# /contracts – Smart contract HRCSafetyLog

Hardhat **3.17.0** + Ignition, Solidity **0.8.28** (pragma `^0.8.24`, optimizer 200 runs), OpenZeppelin Contracts **5.6.1**.

> Người mới bắt đầu với phần này: đọc [docs/NGUOI3_SMART_CONTRACT.md](../docs/NGUOI3_SMART_CONTRACT.md) (quy trình từng bước, vì sao, giải thích code), rồi tự gọi từng hàm bằng `python scripts/thuc_hanh_contract.py`.

## Lệnh

```powershell
cd contracts
npm install                                   # lần đầu
npx hardhat build                             # biên dịch
npx hardhat test                              # toàn bộ test (Solidity fuzz + mocha)
npx hardhat test --gas-stats                  # + bảng gas mọi hàm
npx hardhat test --coverage                   # + độ phủ (HTML ở coverage/html)
npx hardhat test mocha test/GasReport.ts      # bảng gas + so sánh batch vs ghi từng dòng
npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts                      # deploy thử (in-process)
npx hardhat node                                                                  # terminal 1: node local
npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost  # terminal 2
```

`MAX_VIOLATIONS` và danh sách robot lấy từ [hrc_params.json](hrc_params.json). File này **sinh tự động** từ `config/hrc_config.toml` bằng `python scripts/gen_config.py`.

## Thiết kế

**Vai trò**

| Role | Quyền |
|---|---|
| DEFAULT_ADMIN | cấp/thu hồi role, `registerRobot`, `setRobotDevice`, `resetViolations` |
| DEVICE | ví backend. `anchorLog` và `reportIncident` **chỉ cho robot gắn với ví này** |
| OPERATOR | `checkIn` robot. Bị thu hồi tự động khi vi phạm ≥ `MAX_VIOLATIONS` |
| SUPERVISOR | **người duy nhất** gọi được `unlock` |
| AUDITOR | `recordAudit`: ghi kết quả đối soát log lên chuỗi |

**Máy trạng thái robot**

```
          reportIncident (DEVICE)                 unlock (SUPERVISOR)
 ACTIVE ─────────────────────────► LOCKED ──────────────────────────► ACTIVE
                                     │  ▲                     (xóa operator, buộc check-in lại)
                                     └──┘ reportIncident: vẫn ghi, giữ LOCKED
 anchorLog được phép ở cả 2 trạng thái (hộp đen không ngừng ghi)
```

**Chống replay batch:** `batchId` phải đúng bằng `lastBatchId + 1`, và `fromSeq` phải lớn hơn `toSeq` của batch trước. Gửi lại, nhảy cóc hay chồng lấn đều revert.

**Kiểm chứng log:**
- `verifyRecord(batchId, record, proof)` tự tính leaf từ 7 trường của bản ghi.
- `verifyLeaf` giữ theo interface gốc nhưng có rủi ro second-preimage. Test `SECOND-PREIMAGE` chứng minh rủi ro này.

**Không có vòng lặp** nên gas mọi hàm là hằng số (chỉ phụ thuộc độ dài proof). Lỗi báo bằng custom error, mọi hàm có NatSpec.

## Kết quả kiểm thử (2026-09-24)

- **50/50 test pass**: 45 mocha + 5 Solidity fuzz, mỗi fuzz 256 lần chạy.
- Coverage: **100% dòng, 100% statement**.
- Bytecode: 7.112 byte (giới hạn 24.576).

| Hàm | Gas |
|---|---:|
| deploy | 1,623,828 |
| anchorLog (batch 256 bản ghi, ổn định) | 84,540 |
| anchorLog (batch đầu tiên) | 101,478 |
| reportIncident (vi phạm lần 1) | 129,168 |
| reportIncident (lần 3, khóa operator) | 116,206 |
| unlock | 33,279 |
| checkIn | 33,094 |
| recordAudit | 29,896 |
| verifyRecord (view, proof 8 phần tử) | ~35,020 nếu gọi trong tx; miễn phí nếu gọi off-chain |

**1 ca 8 giờ ở 10 Hz (288.000 bản ghi), giá gas giả định 1 gwei:**
- Neo theo batch 256: 1.125 tx, khoảng **0,095 ETH**.
- Ghi từng dòng: 288.000 tx, khoảng **24,35 ETH** (đắt hơn khoảng 256 lần), và cần 10 tx/giây nên không khả thi trên Ethereum.
