# Người 3 – Smart contract: làm gì, vì sao, và code hoạt động thế nào

Tài liệu này dành cho người phụ trách **smart contract** của Nhóm 01, gồm 5 phần:
- **Phần A:** contract nằm ở đâu trong mô hình, giải quyết vấn đề gì.
- **Phần B:** quy trình làm, từng bước. Mỗi bước có **Làm gì**, **Vì sao**, **Giúp gì cho mô hình**, **Kết quả mong đợi**.
- **Phần C:** giải thích **code `HRCSafetyLog.sol`** từng khối.
- **Phần D:** giải thích các file còn lại (deploy, cấu hình, test).
- **Phần E:** contract nói chuyện với backend và dashboard như thế nào.
- **Phần F:** câu hỏi phản biện kèm câu trả lời.

Mọi số liệu gas trong tài liệu đo từ lần chạy thật trên máy nhóm (`npx hardhat test mocha test/GasReport.ts`).

---

## Phần A. Contract là gì trong mô hình này

```
ESP32 ──(ngắt relay NGAY, không cần mạng)──►  robot dừng           ← FAST LANE: cứu người
  │ MQTT + HMAC
  ▼
Backend + AI ──web3.py──► ┌───────────────────────────────┐        ← TRUST LANE: làm chứng
                          │  HRCSafetyLog.sol (phần bạn)  │
Dashboard ──ethers.js───► │  • hộp đen: Merkle root log    │
  (MetaMask ký unlock)    │  • trọng tài: khóa/mở, phân quyền│
                          └───────────────────────────────┘
```

**Contract KHÔNG dừng robot.** Việc dừng robot do ESP32 làm trong vài mili-giây. Contract làm 2 việc mà ESP32 và database thường không làm được:

| Vai trò | Vấn đề nếu không có blockchain | Contract giải quyết thế nào |
|---|---|---|
| **Hộp đen** | Admin database sửa log để che giấu sự cố, và không ai phát hiện | Lưu **Merkle root** của log: sửa 1 dòng là root lệch |
| **Trọng tài** | Nhà máy tự bấm "mở khóa" rồi chối trách nhiệm | Chỉ **SUPERVISOR** mở được, và **mọi lần mở đều lưu vĩnh viễn** ai mở, lúc nào |
| **Định danh** | Không biết ai đang vận hành khi có sự cố | Operator **check-in** bằng ví; sự cố ghi **vi phạm** vào đúng người, 3 lần thì bị khóa quyền |

---

## Phần B. Quy trình làm từng bước

### Bước 0. Chuẩn bị môi trường

**Làm gì**, trong cmd:
```bat
node -v
cd /d D:\Blockchain\Report_CK\contracts
npm install
```

**Vì sao:**
- Contract viết bằng Solidity, nhưng công cụ biên dịch, test và deploy (**Hardhat**) chạy trên **Node.js**.
- `npm install` đọc `package.json` và tải về thư mục `node_modules`:
  - Hardhat 3.17;
  - OpenZeppelin 5.6.1: thư viện phân quyền và Merkle đã được kiểm toán;
  - ethers v6: thư viện gọi contract từ JavaScript;
  - forge-std: dùng cho fuzz test.

**Kết quả mong đợi:** `node -v` in ra v22 trở lên; `npm install` chạy xong không có dòng `ERR!`.

> Luôn đứng **trong thư mục `contracts`** khi gõ `npx hardhat …`. Ở sai thư mục, npx sẽ đòi cài một bản Hardhat khác (lỗi bạn gặp lần trước).

---

### Bước 1. Thiết kế: quyết định cái gì lên chuỗi, cái gì không

**Làm gì:** đọc [docs/INTERFACES.md](INTERFACES.md), mục về contract và leaf Merkle. Đây là "hợp đồng" đã chốt giữa bạn, backend (người 4) và dashboard (người 5).

**Vì sao:** ghi lên blockchain **rất đắt và chậm**. Tính cho 1 ca 8 giờ, với 10 bản tin mỗi giây:

| Cách | Số giao dịch | Chi phí (gas 1 gwei) | Khả thi? |
|---|---|---|---|
| Ghi từng dòng log | 288.000 | ≈ **24,35 ETH** | ❌ Sepolia 12 s mới có 1 khối, không kịp 10 tx/giây |
| Gom 256 dòng → 1 Merkle root | 1.125 | ≈ **0,095 ETH** | ✅ rẻ hơn khoảng 256 lần |

Kết luận thiết kế:
- **Lên chuỗi:** Merkle root của từng batch, sự cố, trạng thái khóa, quyền và vi phạm. Đây là những thứ **cần bất biến và cần trọng tài**.
- **Không lên chuỗi:** log thô, nằm trong SQLite. Vẫn kiểm chứng được nhờ root.

**Giúp gì cho mô hình:** đây là quyết định lớn nhất của phần bạn. Nó làm hệ thống **vừa rẻ, vừa không sửa được log**.

---

### Bước 2. Lấy tham số từ cấu hình dùng chung

**Làm gì:** xem [contracts/hrc_params.json](../contracts/hrc_params.json). Đừng sửa tay file này: nó được **sinh tự động** từ `config/hrc_config.toml` bằng `python scripts/gen_config.py`.

```json
{ "robotIds": [1], "maxViolations": 3, "batchMaxRecords": 256, ... }
```

**Vì sao:** cả nhóm dùng **một nguồn cấu hình duy nhất**. Nếu contract ghi cứng "3 lần vi phạm" mà backend lại nghĩ là 5, hệ thống sẽ lệch nhau. Đổi ở `hrc_config.toml` thì firmware, backend và contract cùng đổi theo.

**Giúp gì:** Ignition dùng `maxViolations` khi deploy, `robotIds` để đăng ký robot; test dùng `batchMaxRecords` để đo gas đúng kích thước batch thật.

---

### Bước 3. Viết contract

**Làm gì:** viết [contracts/contracts/HRCSafetyLog.sol](../contracts/contracts/HRCSafetyLog.sol). Giải thích chi tiết ở **Phần C**.

**Vì sao làm theo thứ tự này:**
1. Vai trò và kiểu dữ liệu: ai được làm gì, lưu những gì.
2. Event và error: contract "nói" gì ra ngoài.
3. Các hàm, theo đúng luồng nghiệp vụ: đăng ký → ghi log → sự cố → mở khóa → kiểm toán.

---

### Bước 4. Biên dịch

**Làm gì:**
```bat
npx hardhat compile
```

**Vì sao:** máy Ethereum (EVM) không hiểu Solidity, chỉ hiểu **bytecode**. Trình biên dịch `solc 0.8.28` sinh ra hai thứ:
- **bytecode:** code gửi lên chuỗi khi deploy;
- **ABI (Application Binary Interface):** "bảng mục lục" liệt kê tên hàm, tham số và event.

**Giúp gì cho mô hình:** file `contracts/artifacts/contracts/HRCSafetyLog.sol/HRCSafetyLog.json` chứa ABI. **Backend đọc đúng file này** để biết gọi `anchorLog` thế nào (xem `backend/hrc_backend/chain.py`, biến `ARTIFACT_PATH`). Vì vậy, sau khi sửa contract phải compile lại, nếu không backend dùng ABI cũ.

**Kết quả mong đợi:** `Compiled 1 Solidity file…`, hoặc `Nothing to compile` nếu chưa sửa gì.

---

### Bước 5. Viết và chạy test

**Làm gì:**
```bat
npx hardhat test
```

**Vì sao:** contract **đã deploy thì không sửa được**. Có lỗi thì phải deploy contract mới, và mất toàn bộ lịch sử cũ. Vì thế phải test kỹ trước. Có 2 loại test:

| Loại | File | Cách làm | Bắt được lỗi gì |
|---|---|---|---|
| **Test kịch bản (mocha)** | `test/HRCSafetyLog.ts`, 45 test | Người viết nghĩ ra từng tình huống: "OPERATOR gọi unlock thì phải bị từ chối" | Lỗi logic nghiệp vụ, sai quyền |
| **Fuzz test** | `contracts/HRCSafetyLog.t.sol`, 5 test × 256 lần | Máy **tự sinh input ngẫu nhiên** | Trường hợp biên người viết không nghĩ tới (số rất lớn, seq bằng 0…) |

**Kết quả mong đợi:** **50 passing**. Nếu có test đỏ, **không được deploy**.

---

### Bước 6. Đo gas

**Làm gì:**
```bat
npx hardhat test mocha test/GasReport.ts
```

**Vì sao:** gas là **tiền thật** trên mainnet. Cần con số cụ thể để:
1. chứng minh thiết kế Merkle rẻ;
2. đưa vào báo cáo;
3. biết cần bao nhiêu SepoliaETH cho demo.

**Kết quả thật đo trên máy nhóm:**

| Hàm | Gas | Ghi chú |
|---|---|---|
| deploy | 1.623.828 | chỉ 1 lần |
| `anchorLog` (batch 256 bản ghi) | **84.540** | lần đầu 101.478, vì ghi vào ô nhớ mới tinh |
| `reportIncident` | 122.235 | lưu sự cố + đổi trạng thái |
| `unlock` | 33.279 | |
| `checkIn` | 33.094 | |
| `recordAudit` | 29.896 | **chỉ emit event, không ghi storage**, nên rẻ nhất |

Điểm đáng nói khi bảo vệ: `anchorLog` cho 256 bản ghi (84.540) **gần bằng** cho 1 bản ghi (84.552). **Chi phí không phụ thuộc số dòng log**, vì dù batch lớn đến đâu, trên chuỗi vẫn chỉ lưu 1 root 32 byte.

---

### Bước 7. Đo độ phủ test (coverage)

**Làm gì:**
```bat
npx hardhat test --coverage
```

**Vì sao:** coverage cho biết test đã chạy qua bao nhiêu phần trăm dòng code. Dòng nào chưa được test chạy qua thì có thể còn lỗi mà không ai biết.

**Kết quả mong đợi:** **100%** dòng và statement của `HRCSafetyLog.sol`.

---

### Bước 8. Deploy lên chuỗi local bằng Ignition

**Làm gì**, dùng 2 cửa sổ cmd:
```bat
:: cửa sổ 1 – giữ chạy
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat node

:: cửa sổ 2
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset
```

**Vì sao:**
- `npx hardhat node` dựng một **blockchain giả trên máy**, gồm 20 ví, mỗi ví có sẵn 10.000 ETH giả. Tạo khối tức thì, miễn phí, nên hợp để phát triển.
- Chỉ deploy contract thì **chưa đủ**: còn phải cấp 4 vai trò và đăng ký robot. **Ignition** gom toàn bộ các bước đó vào một kịch bản, chạy đúng thứ tự, và ghi lại địa chỉ contract vào `ignition/deployments/chain-31337/deployed_addresses.json`.
- `--reset` bỏ bản ghi deploy cũ để deploy mới hoàn toàn. Cần dùng mỗi lần khởi động lại node, vì node mất sạch dữ liệu khi tắt.

**Giúp gì cho mô hình:** backend **tự đọc địa chỉ contract** từ file `deployed_addresses.json`, nên không ai phải chép địa chỉ bằng tay.

**Kết quả mong đợi:**
```
Executed HRCSafetyLogModule#grantAuditor
Executed HRCSafetyLogModule#grantDevice
Executed HRCSafetyLogModule#grantOperator
Executed HRCSafetyLogModule#grantSupervisor
Executed HRCSafetyLogModule#registerRobot_1
[ HRCSafetyLogModule ] successfully deployed 🚀
HRCSafetyLogModule#HRCSafetyLog - 0x5FbDB2315678afecb367f032d93F642f64180aa3
```

---

### Bước 9. Tự tay gọi từng hàm: script thực hành

**Làm gì**, sau bước 8, trong cửa sổ cmd thứ 3:
```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python scripts\thuc_hanh_contract.py
```

Script đi qua 11 bước, **dừng chờ Enter** sau mỗi bước. Mỗi bước in ra đang làm gì, vì sao, và contract trả lời ra sao.

| Bước | Bạn sẽ thấy | Dòng code tương ứng (Phần C) |
|---|---|---|
| 1 | ADMIN đăng ký robot 2 (48.312 gas) | `registerRobot` |
| 2 | OPERATOR gọi `unlock` bị từ chối `AccessControlUnauthorizedAccount` | `onlyRole(...)` |
| 3 | DEVICE neo batch 4 bản ghi; root trên chuỗi khớp root tính ở Python | `anchorLog` |
| 4 | Gửi lại batch cũ → `InvalidBatchId`; seq chồng lấn → `SeqOverlap` | chống replay trong `anchorLog` |
| 5 | Bản ghi gốc → `verifyRecord = True`; sửa khoảng cách → `False` | `verifyRecord`, `leafHash` |
| 6 | OPERATOR check-in | `checkIn` |
| 7 | DEVICE báo sự cố → robot **LOCKED** | `reportIncident` |
| 8 | Check-in → `RobotIsLocked`; DEVICE tự unlock → bị từ chối | |
| 9 | SUPERVISOR unlock → **ACTIVE**, operator bị xóa; unlock lần 2 → `RobotNotLocked` | `unlock` |
| 10 | AUDITOR ghi kết quả; batch không tồn tại → `BatchNotFound` | `recordAudit` |
| 11 | In toàn bộ **event** vừa phát ra | các `emit` |

**Vì sao cần bước này:** đọc test thì biết contract *đúng*; tự gọi thì **hiểu** nó *làm gì*. Khi bảo vệ, thầy hỏi "nếu operator tự mở khóa thì sao?", bạn trả lời được vì đã thấy tận mắt.

Script dùng **robot 2** nên không ảnh hưởng robot 1 của backend, và chạy lại bao nhiêu lần cũng được.

---

### Bước 10. Kiểm tra tích hợp với cả hệ thống

**Làm gì:** chạy toàn hệ thống theo [HUONG_DAN_CHAY.md](HUONG_DAN_CHAY.md), gồm backend, dashboard và simulator với kịch bản `approach`.

**Vì sao:** contract đúng một mình chưa đủ. Cần chắc backend gọi đúng hàm, và dashboard đọc đúng event.

**Kết quả mong đợi:** dashboard hiện robot **LOCKED** và bảng "Lịch sử sự cố on-chain" có dòng mới. Cả hai được **đọc thẳng từ contract của bạn**, không qua backend.

---

### Bước 11. Deploy lên Sepolia (testnet thật) và verify trên Etherscan

**Làm gì:**
1. Chuẩn bị:
   - RPC Sepolia miễn phí từ Alchemy;
   - ví MetaMask **chỉ dùng để test**, có khoảng 0,05 SepoliaETH lấy từ faucet;
   - API key Etherscan.
2. Tạo file cấu hình: `copy .env.sepolia.example .env.sepolia`, rồi điền `SEPOLIA_RPC_URL`, `SEPOLIA_PRIVATE_KEY`, `SUPERVISOR_ADDRESS`, `ETHERSCAN_API_KEY`. **Không gửi file này cho ai.**
3. Deploy:
   ```bat
   python scripts\deploy_sepolia.py --dry-run-local   :: tập dượt trên node local trước
   python scripts\deploy_sepolia.py --verify          :: làm thật
   ```

**Vì sao:**
- Đề bài yêu cầu có **testnet**. Chuỗi local chỉ tồn tại trên máy bạn, còn Sepolia thì **ai cũng kiểm tra được**.
- **Verify** công bố mã nguồn lên Etherscan. Ai cũng đọc được contract làm gì, và so được bytecode trên chuỗi với mã nguồn. Đây chính là tính **minh bạch** của smart contract.
- Nên chạy `--dry-run-local` trước để không tốn ETH vào một lần deploy lỗi.

**Kết quả mong đợi:** có link `https://sepolia.etherscan.io/address/0x…` hiển thị tab **Contract ✓** (đã verify). Đưa link này vào báo cáo.

---

### Bước 12. Viết báo cáo phần contract

Nội dung cần có:
1. Sơ đồ 5 vai trò.
2. Sơ đồ trạng thái ACTIVE ⇄ LOCKED.
3. Hình minh họa Merkle tree.
4. Bảng gas (bước 6) và bảng so sánh batch với ghi từng dòng (bước 1).
5. Kết quả test: 50 passing, coverage 100%.
6. Link Etherscan.
7. Giới hạn: oracle problem, và hàm `verifyLeaf` có rủi ro second-preimage (Phần C, mục 9).

---

## Phần C. Giải thích code `HRCSafetyLog.sol`

File dài 464 dòng, chia 8 khối. Số dòng ghi trong ngoặc để bạn mở file đối chiếu.

### C1. Phần đầu file (dòng 1–20)

```solidity
pragma solidity ^0.8.24;
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";
contract HRCSafetyLog is AccessControl {
```

- `pragma solidity ^0.8.24`: yêu cầu trình biên dịch từ 0.8.24 trở lên. Từ bản 0.8, **cộng tràn số sẽ tự báo lỗi**, nên không cần thư viện SafeMath.
- `import`: dùng lại 2 thư viện của **OpenZeppelin**, bộ thư viện contract phổ biến nhất và đã được kiểm toán. **Tự viết phân quyền là nguồn lỗi bảo mật số 1**, nên dùng lại code đã được kiểm chứng.
- `is AccessControl`: **kế thừa**. Contract có sẵn các hàm `grantRole`, `revokeRole`, `hasRole` và modifier `onlyRole`.

### C2. Vai trò (dòng 26–32)

```solidity
bytes32 public constant DEVICE_ROLE = keccak256("DEVICE_ROLE");
```

- Mỗi vai trò là một **mã 32 byte**, lấy bằng cách hash tên vai trò. Hash giúp các vai trò không bao giờ trùng mã nhau.
- `constant`: giá trị cố định, được nhúng thẳng vào bytecode, nên **đọc không tốn gas storage**.
- `public`: Solidity tự sinh hàm đọc `DEVICE_ROLE()`. Backend và test dùng hàm này để lấy mã.
- Vai trò thứ 5, `DEFAULT_ADMIN_ROLE` (mã `0x00…00`), có sẵn trong AccessControl.

### C3. Kiểu dữ liệu (dòng 39–80)

```solidity
enum RobotState { ACTIVE, LOCKED }        // 0 = ACTIVE, 1 = LOCKED

struct Robot {
    address device;          // ví DEVICE được ghi thay robot này
    RobotState state;
    bool registered;
    uint64 lastBatchId;      // batch cuối đã neo
    uint64 lastToSeq;        // seq cuối của batch cuối
    address currentOperator; // ai đang vận hành
}
```

- `enum`: tập giá trị có tên. Trong bộ nhớ, mỗi giá trị là một số: ACTIVE = 0, LOCKED = 1. Backend so sánh `state == 1`.
- `struct`: gom nhiều trường thành một "bản ghi".

**Tối ưu gas (chú thích "gói vào 2 slot"):**
- Storage EVM chia thành các **ô 32 byte (slot)**, và **ghi 1 slot mới tốn khoảng 20.000 gas**.
- `address` (20 byte) + `enum` (1) + `bool` (1) + `uint64` (8) = 30 byte, **vừa 1 slot**. `lastToSeq` + `currentOperator` = 28 byte, thêm 1 slot nữa.
- Xếp các trường đúng thứ tự như vậy thì tốn 2 slot thay vì 6.
- Đây là lý do dùng `uint64` cho seq thay vì `uint256`: 64 bit vẫn đủ đếm tới 1,8 × 10¹⁹.

`LogRecord` là **một dòng telemetry** với 7 trường, **đúng thứ tự** của leaf Merkle đã chốt. Thứ tự này phải giống hệt ở Python (`hrc_common/merkle.py`) và TypeScript (`test/helpers/merkle.ts`); sai thứ tự là hash khác hoàn toàn.

### C4. Storage: những gì contract "nhớ" (dòng 87–97)

```solidity
uint256 public immutable MAX_VIOLATIONS;
uint256 public incidentCount;
mapping(address operator => uint256 count) public violationCount;
mapping(uint256 robotId => Robot) private _robots;
mapping(uint256 robotId => mapping(uint64 batchId => Batch)) private _batches;
mapping(uint256 incidentId => Incident) private _incidents;
```

- `immutable`: gán **một lần trong constructor** rồi không bao giờ đổi. Rẻ như `constant`, nhưng giá trị được truyền vào lúc deploy (lấy từ `hrc_params.json`).
- `mapping`: giống từ điển (dictionary). Tra theo khóa rất rẻ, nhưng **không liệt kê được**. Muốn xem hết sự cố thì đọc **event**.
- `mapping` lồng nhau `_batches[robotId][batchId]`: mỗi robot có dãy batch riêng.
- `private`: không tự sinh hàm đọc. Thay vào đó có các hàm view `getRobot`, `getBatch`, `getIncident` (dòng 421–438) trả về cả struct.

> `private` **không có nghĩa là bí mật**: mọi dữ liệu trên blockchain đều công khai, ai cũng đọc được storage. `private` chỉ nghĩa là contract khác không gọi trực tiếp được.

### C5. Event và error (dòng 104–167)

```solidity
event EmergencyStop(uint256 indexed robotId, uint256 indexed incidentId, address indexed operator,
                    bytes32 evidenceHash, uint8 severity, uint256 timestamp);
error RobotIsLocked(uint256 robotId);
```

**Event:**
- Là thông báo contract phát ra, được lưu trong **log của giao dịch**. Rẻ hơn storage khoảng 10 lần, nhưng contract **không đọc lại được**; chỉ bên ngoài đọc được.
- `indexed`: đánh chỉ mục để **lọc nhanh**, ví dụ "mọi sự cố của robot 1". Mỗi event có tối đa 3 trường indexed.
- **Event là cầu nối giữa contract với phần còn lại:**
  - backend nghe `EmergencyStop` rồi gửi LOCK xuống robot, nghe `Unlocked` rồi gửi UNLOCK;
  - dashboard đọc `EmergencyStop` và `Unlocked` để hiện bảng lịch sử.

**Custom error:**
- Khi điều kiện sai, `revert RobotIsLocked(robotId)` **hủy toàn bộ giao dịch** và trả về tên lỗi kèm tham số.
- So với `require(cond, "chuỗi dài")`, custom error **rẻ hơn** (chỉ 4 byte mã lỗi) và **mang dữ liệu**. Ví dụ `InvalidBatchId(robotId=2, expected=3, provided=2)` cho biết ngay lỗi ở đâu. Script thực hành in đúng các dòng này.

### C6. Constructor và quản trị (dòng 177–221)

```solidity
constructor(address admin, uint256 maxViolations) {
    if (admin == address(0)) revert ZeroAddress();
    if (maxViolations == 0) revert InvalidMaxViolations();
    MAX_VIOLATIONS = maxViolations;
    _grantRole(DEFAULT_ADMIN_ROLE, admin);
}
```

- `constructor` chạy **đúng 1 lần** lúc deploy.
- Kiểm `address(0)`: nếu admin là ví 0 (không ai có private key của nó), contract sẽ **mãi mãi không ai quản trị được**. Chặn ngay từ đầu.
- `maxViolations == 0` thì mọi operator bị khóa ngay từ lần vi phạm đầu, nên cũng vô lý và bị chặn.

**`registerRobot(robotId, device)`:** chỉ admin gọi được. Nó gắn robot với **1 ví DEVICE**; từ đó chỉ đúng ví này được ghi log cho robot đó. Đây là **định danh robot trên chuỗi**.

**`setRobotDevice`:** **đổi khóa** khi ví DEVICE bị lộ. Lịch sử batch cũ vẫn giữ nguyên, chỉ ví mới ghi được từ đây.

**`resetViolations`:** admin xóa điểm vi phạm sau khi operator được đào tạo lại. Hàm phát event `ViolationsReset`, nên **việc tha lỗi cũng để lại dấu vết**.

### C7. `anchorLog`: neo log (dòng 239–256), hàm quan trọng nhất

```solidity
function anchorLog(uint256 robotId, uint64 batchId, bytes32 merkleRoot, uint64 fromSeq, uint64 toSeq) external {
    Robot storage r = _requireRobotDevice(robotId);                         // (1) đúng ví DEVICE của robot?
    if (merkleRoot == bytes32(0)) revert ZeroMerkleRoot();                  // (2) root rỗng => vô nghĩa
    if (fromSeq > toSeq) revert InvalidSeqRange(fromSeq, toSeq);            // (3) dải seq ngược

    uint64 lastBatchId = r.lastBatchId;
    uint64 expected = lastBatchId + 1;
    if (batchId != expected) revert InvalidBatchId(robotId, expected, batchId);          // (4) chống replay/nhảy cóc
    if (lastBatchId != 0 && fromSeq <= r.lastToSeq) revert SeqOverlap(...);              // (5) chống chồng lấn

    r.lastBatchId = batchId;                                                 // (6) cập nhật
    r.lastToSeq = toSeq;
    _batches[robotId][batchId] = Batch(merkleRoot, fromSeq, toSeq, uint64(block.timestamp));
    emit LogAnchored(robotId, batchId, merkleRoot, fromSeq, toSeq, block.timestamp);     // (7) thông báo
}
```

Giải thích từng điểm:
1. **Kiểm quyền 2 lớp**, trong `_requireRobotDevice` (dòng 459): người gọi phải có `DEVICE_ROLE` **và** phải đúng là ví gắn với robot này. Ví DEVICE của robot 2 không ghi được cho robot 1.
2. Root bằng 0 nghĩa là không có dữ liệu. Chặn để `getBatch` dùng "root = 0" làm dấu hiệu "batch không tồn tại" (xem `recordAudit`, `verifyRecord`).
3. `fromSeq > toSeq` là dữ liệu hỏng.
4. **Chống phát lại:** batch phải đánh số liên tục 1, 2, 3… Kẻ gian không thể gửi lại batch 5 để **ghi đè** batch 5 cũ (lúc đó hệ thống đòi batch 6), cũng không thể nhảy cóc.
5. **Chống chồng lấn:** batch mới phải bắt đầu **sau** seq cuối của batch trước. Nếu không, cùng một bản ghi seq 100 có thể nằm trong 2 batch với 2 nội dung khác nhau, và không biết tin bản nào. Batch được phép có **khoảng trống** seq (robot mất mạng một lúc); chỉ cấm chồng lấn.
6. `Robot storage r`: `storage` là **tham chiếu** thẳng tới dữ liệu trên chuỗi, nên sửa `r` là sửa luôn trên chuỗi. Nếu dùng `memory` thì chỉ sửa trên bản sao.
7. `block.timestamp`: giờ của khối chứa giao dịch. Giờ này do mạng lưới quyết định, người gọi không tự khai được.

**Có một điều cố ý không kiểm:** `anchorLog` **vẫn chạy khi robot LOCKED**. Hộp đen máy bay không ngừng ghi khi máy bay gặp sự cố; đây là lúc log quan trọng nhất.

**Chi phí không đổi:** trong contract **không có vòng lặp**, nên gas của `anchorLog` là hằng số, không phụ thuộc số bản ghi.

### C8. `reportIncident`, `unlock`, `checkIn`, `checkOut` (dòng 269–337)

```solidity
function reportIncident(uint256 robotId, bytes32 evidenceHash, uint8 severity, address operator)
    external returns (uint256 incidentId)
{
    Robot storage r = _requireRobotDevice(robotId);
    if (evidenceHash == bytes32(0)) revert ZeroEvidenceHash();
    if (severity < MIN_SEVERITY || severity > MAX_SEVERITY) revert InvalidSeverity(severity);

    incidentId = ++incidentCount;                      // id sự cố: 1, 2, 3…
    _incidents[incidentId] = Incident(evidenceHash, robotId, operator, severity, uint64(block.timestamp));
    r.state = RobotState.LOCKED;                       // KHÓA robot
    emit EmergencyStop(robotId, incidentId, operator, evidenceHash, severity, block.timestamp);

    if (operator != address(0)) {                      // có quy lỗi cho người?
        uint256 v = ++violationCount[operator];
        emit ViolationRecorded(operator, v);
        if (v >= MAX_VIOLATIONS && _revokeRole(OPERATOR_ROLE, operator)) {
            emit OperatorBlocked(operator, v);         // đủ 3 lần => THU HỒI quyền
        }
    }
}
```

- **`evidenceHash`:** backend truyền **Merkle root của 20 bản ghi quanh thời điểm sự cố**. Bằng chứng nằm off-chain, còn "dấu vân tay" của nó nằm on-chain.
- **`severity` 1–3:**
  - 1: lỗi cảm biến kéo dài.
  - 2: người xâm nhập, hoặc robot vượt tốc.
  - 3: người trong vùng DANGER **mà relay không ngắt**, tức fast lane hỏng.
- **`operator`:** backend chỉ truyền địa chỉ operator khi sự cố **do người** (INTRUSION). Các lỗi do máy như vượt tốc hay cảm biến lỗi thì truyền `address(0)`, **không quy lỗi oan** cho người (`backend/hrc_backend/incidents.py`, `blame_operator`).
- **`_revokeRole` trả về `true/false`:** trả `false` nếu người đó đã bị thu hồi quyền từ trước. Nhờ vậy event `OperatorBlocked` **chỉ phát đúng 1 lần**, dù vi phạm lần 4, lần 5.
- **Sự cố thứ 2 khi robot đang LOCKED** vẫn được ghi, và robot giữ trạng thái LOCKED.

```solidity
function unlock(uint256 robotId) external onlyRole(SUPERVISOR_ROLE) {
    Robot storage r = _requireRegistered(robotId);
    if (r.state != RobotState.LOCKED) revert RobotNotLocked(robotId);
    r.state = RobotState.ACTIVE;
    address op = r.currentOperator;
    if (op != address(0)) { r.currentOperator = address(0); emit OperatorCheckedOut(robotId, op); }
    emit Unlocked(robotId, msg.sender, block.timestamp);
}
```

- **`onlyRole(SUPERVISOR_ROLE)`** là **dòng quan trọng nhất của cả đồ án**. Chỉ SUPERVISOR mở khóa được: backend không, admin không, operator cũng không.
- `msg.sender`: địa chỉ ví **đã ký giao dịch**. Không giả mạo được, vì muốn ký phải có private key.
- **Xóa operator hiện tại:** sau sự cố, người vận hành phải check-in lại từ đầu. Đó là "ca mới", có người chịu trách nhiệm mới.
- Unlock khi robot đang ACTIVE → `RobotNotLocked`. Mục đích là tránh event `Unlocked` "rác" gây nhiễu lịch sử.

```solidity
function checkIn(uint256 robotId) external onlyRole(OPERATOR_ROLE) {
    ...
    if (r.state == RobotState.LOCKED) revert RobotIsLocked(robotId);                 // robot đang khóa
    if (current != address(0) && current != msg.sender) revert RobotBusy(robotId, current);  // người khác đang giữ
    r.currentOperator = msg.sender;
}
function checkOut(uint256 robotId) external { ... }   // KHÔNG cần role
```

`checkOut` cố ý **không đòi role**. Operator vừa bị khóa quyền (mất `OPERATOR_ROLE`) vẫn phải **rời robot** được, nếu không robot bị "kẹt" với một người không còn quyền.

### C9. Kiểm toán và kiểm chứng Merkle (dòng 347–418)

**`recordAudit`:** AUDITOR ghi kết quả đối soát. Hàm **chỉ `emit`, không ghi storage**, nên là hàm rẻ nhất (29.896 gas). Kết quả audit chỉ cần làm bằng chứng lịch sử, không cần contract đọc lại.

**Merkle tree hoạt động thế nào**, lấy ví dụ 4 bản ghi như bước 5 của script thực hành:

```
                ROOT = H( H12 , H34 )            ← lưu trên chuỗi (anchorLog)
               /                    \
        H12 = H(L1,L2)          H34 = H(L3,L4)
         /       \               /       \
       L1        L2            L3        L4     ← leaf = keccak256(abi.encode(7 trường))
     seq 1     seq 2         seq 3     seq 4    ← nằm trong SQLite
```

- Muốn chứng minh **L2** thuộc batch, chỉ cần gửi **proof = [L1, H34]**. Contract tính `H(L1,L2)` = H12, rồi `H(H12,H34)`, rồi so với ROOT.
- 256 bản ghi thì proof chỉ có **8 hash** (vì 2⁸ = 256).
- **Sorted-pair:** khi ghép 2 hash, luôn đặt hash **nhỏ hơn** lên trước (`hashPair` trong `helpers/merkle.ts`). Nhờ vậy proof không cần ghi "trái" hay "phải", và tương thích với `MerkleProof` của OpenZeppelin.

```solidity
function leafHash(LogRecord calldata record) public pure returns (bytes32) {
    return keccak256(abi.encode(record.robotId, record.seq, record.tsMs, record.distanceMm,
                                record.vRobotMmps, record.zone, record.estop));
}
```

- `abi.encode`: mỗi trường được đệm thành đúng 32 byte, tổng 7 × 32 = **224 byte**. Không dùng `abi.encodePacked` (nối sát nhau) vì dễ bị **trùng**: ví dụ "1" + "23" và "12" + "3" nối ra cùng một chuỗi.
- `pure`: không đọc, không ghi chuỗi, chỉ tính toán.
- `calldata`: đọc dữ liệu thẳng từ tham số, không chép vào bộ nhớ, nên rẻ hơn `memory`.

**`verifyLeaf` và `verifyRecord`: vì sao có cả hai**

| | `verifyLeaf(robotId, batchId, leaf, proof)` | `verifyRecord(batchId, record, proof)` |
|---|---|---|
| Nhận vào | hash leaf đã tính sẵn | **dữ liệu gốc** 7 trường |
| Contract tự tính leaf? | không | **có** |
| Kiểm seq nằm trong dải batch? | không | **có** |
| An toàn | ⚠️ có rủi ro **second-preimage** | ✅ |

**Second-preimage** là gì: kẻ gian có thể lấy một **node trung gian**, ví dụ H12, rồi khai "đây là một leaf", kèm proof ngắn hơn là [H34]. `verifyLeaf` vẫn trả về `true`, dù H12 không phải một bản ghi thật.

`verifyRecord` chặn được việc này, vì contract **tự tính leaf từ 224 byte dữ liệu**, còn node trung gian được hash từ 64 byte. Hai loại này không thể trùng nhau.

`verifyLeaf` được giữ lại vì nằm trong interface đã chốt, còn `verifyRecord` là bản an toàn được nhóm duyệt bổ sung. Có một test riêng chứng minh điều này: `"SECOND-PREIMAGE: verifyLeaf bị lừa bởi node trung gian, verifyRecord thì không"`.

`MerkleProof.verifyCalldata` là hàm của OpenZeppelin, không tự viết lại.

### C10. Hàm nội bộ (dòng 445–463)

```solidity
function _grantRole(bytes32 role, address account) internal override returns (bool) {
    if (role == OPERATOR_ROLE) {
        uint256 v = violationCount[account];
        if (v >= MAX_VIOLATIONS) revert OperatorIsBlocked(account, v);
    }
    return super._grantRole(role, account);
}
```

- `override`: **ghi đè** hàm gốc của AccessControl, chèn thêm một điều kiện rồi gọi lại bản gốc qua `super`.
- **Chặn lách luật:** không có đoạn này, admin chỉ cần `grantRole(OPERATOR_ROLE, người_bị_khóa)` là người đó quay lại làm việc, và bộ đếm vi phạm trở nên vô nghĩa. Giờ admin **bắt buộc** phải gọi `resetViolations` trước, và hàm đó để lại event `ViolationsReset` trên chuỗi.
- Đặt ở `_grantRole` (hàm nội bộ) thay vì `grantRole` (hàm công khai) thì **mọi đường cấp quyền** đều bị chặn.

`_requireRegistered` và `_requireRobotDevice` là các hàm kiểm tra dùng chung, nên không phải lặp lại cùng một đoạn code ở nhiều hàm.

---

## Phần D. Các file còn lại

### D1. `ignition/modules/HRCSafetyLog.ts`: kịch bản deploy

```ts
const admin = m.getAccount(0);                                        // ví gửi giao dịch deploy
const device = m.getParameter("device", HARDHAT_DEFAULTS.device);     // có thể truyền vào, mặc định = Hardhat #1
const hrcSafetyLog = m.contract("HRCSafetyLog", [admin, maxViolations]);   // deploy + gọi constructor
m.call(hrcSafetyLog, "grantRole", [id("DEVICE_ROLE"), device], { id: "grantDevice" });
for (const robotId of HRC.robotIds) m.call(hrcSafetyLog, "registerRobot", [BigInt(robotId), device], ...);
```

- `m.getParameter(tên, mặc_định)`:
  - trên local, dùng địa chỉ mặc định là các ví Hardhat #1–#4;
  - trên Sepolia, `deploy_sepolia.py` truyền địa chỉ thật qua file tham số.
- **Mặc định là địa chỉ cố định**, không phải `m.getAccount(1)`. Trên Sepolia chỉ có 1 ví, và Ignition vẫn kiểm giá trị mặc định `getAccount(1..4)` ngay cả khi đã truyền tham số, nên báo lỗi **HHE10723**. Lỗi này được phát hiện khi tập dượt deploy Sepolia.
- `id("DEVICE_ROLE")` trong ethers chính là `keccak256("DEVICE_ROLE")`, ra đúng mã vai trò như trong contract.
- Ignition **tự sắp thứ tự**: `grantRole` chỉ chạy sau khi deploy xong; các lệnh `grantRole` độc lập với nhau thì chạy cùng một đợt ("Batch #2" trong log).

### D2. `hardhat.config.ts`

- `solidity`: dùng solc 0.8.28 và bật **optimizer 200 runs**. Test và deploy dùng **cùng một cấu hình**, nên gas đo được bằng gas thực tế.
- `networks`:

  | Mạng | Dùng khi nào |
  |---|---|
  | `hardhatMainnet` | chuỗi ảo trong bộ nhớ, dùng khi chạy test |
  | `localhost` | node `npx hardhat node` |
  | `sepolia` | testnet |

- `configVariable("SEPOLIA_PRIVATE_KEY")`: **không ghi private key vào code**. Giá trị lấy từ biến môi trường, và chỉ được đọc khi thật sự dùng mạng sepolia.

### D3. `test/helpers/merkle.ts`

Dựng cây Merkle bằng TypeScript để test, và **phải cho ra đúng root như bản Python** `hrc_common/merkle.py` mà backend dùng. `tests/test_merkle.py` kiểm điều này bằng bộ dữ liệu mẫu chung `tests/fixtures/merkle_vectors.json`.

Nếu 3 nơi (Solidity, TypeScript, Python) tính lệch nhau dù chỉ 1 byte, **mọi lần đối soát đều báo "bị sửa"**, dù log không bị sửa.

- `getProof`: với mỗi tầng, lấy node **anh em** (`index ^ 1`, tức đổi bit cuối: 0↔1, 2↔3…), rồi đi lên tầng trên (`index >>= 1`, tức chia 2).
- Node lẻ không có anh em thì được đẩy thẳng lên tầng trên.

### D4. `test/HRCSafetyLog.ts`: đọc hiểu 1 test mẫu

Test "chỉ SUPERVISOR unlock được" (dòng 289–301):

```ts
it("chỉ SUPERVISOR unlock được; emit Unlocked(robotId, supervisor, timestamp)", async function () {
  const { log, device, supervisor, operator, admin } = await networkHelpers.loadFixture(deployFixture);
  await log.connect(device).reportIncident(R1, EVIDENCE, 2, ZeroAddress);      // chuẩn bị: khóa robot
  for (const who of [operator, device, admin]) {                               // 3 người KHÔNG phải supervisor
    await expect(log.connect(who).unlock(R1))
      .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")  // => đều bị từ chối
      .withArgs(who.address, ROLE.SUPERVISOR);                                 //    và lỗi nói rõ thiếu quyền gì
  }
  const tx = await log.connect(supervisor).unlock(R1);                         // supervisor mở
  const block = await ethers.provider.getBlock(tx.blockNumber!);
  await expect(tx).to.emit(log, "Unlocked").withArgs(R1, supervisor.address, BigInt(block!.timestamp));
  expect(await log.robotState(R1)).to.equal(ACTIVE);                           // robot về ACTIVE
});
```

Chú ý: **cả admin cũng bị từ chối**. Test chứng minh admin không phải "siêu người dùng" trong việc mở khóa.

- `deployFixture`: deploy contract, cấp quyền, đăng ký robot. `loadFixture` **chụp nhanh** trạng thái chuỗi sau lần chạy đầu, rồi các test sau quay về đúng trạng thái đó. Nhờ vậy test chạy nhanh và **không ảnh hưởng lẫn nhau**.
- `log.connect(operator)`: gọi hàm **dưới danh nghĩa** ví operator.
- `revertedWithCustomError(…)`: khẳng định giao dịch **phải thất bại** với đúng lỗi này. Test kiểm cả đường sai, không chỉ đường đúng.

45 test chia thành 7 nhóm (`describe`):
1. khởi tạo;
2. đăng ký robot và phân quyền;
3. `anchorLog` chống replay và chồng lấn;
4. luồng ACTIVE → LOCKED → ACTIVE;
5. operator: check-in, vi phạm, bị khóa;
6. Merkle;
7. AUDITOR.

### D5. `contracts/HRCSafetyLog.t.sol`: fuzz test

```solidity
function testFuzz_OverlapAlwaysReverts(uint64 from1, uint64 len1, uint64 from2, uint64 len2) public {
    from1 = uint64(bound(from1, 0, type(uint64).max / 2));   // bound: ép số ngẫu nhiên vào khoảng hợp lý
    ...
    vm.prank(DEVICE);                                        // lần gọi kế tiếp mang danh DEVICE
    hrc.anchorLog(ROBOT, 1, keccak256("r1"), from1, to1);
    vm.expectRevert(abi.encodeWithSelector(HRCSafetyLog.SeqOverlap.selector, ROBOT, to1, from2));
    vm.prank(DEVICE);
    hrc.anchorLog(ROBOT, 2, keccak256("r2"), from2, to2);    // chồng lấn => LUÔN phải bị từ chối
}
```

- Tham số của hàm test được **máy sinh ngẫu nhiên 256 lần**. Test khẳng định một **tính chất luôn đúng**, chứ không phải một ví dụ cụ thể. Ví dụ: *"với MỌI cặp batch chồng lấn, contract đều từ chối"*.
- `vm.prank` và `vm.expectRevert` là "cheatcode" của forge-std, dùng để giả danh người gọi và khai báo lỗi mong đợi.
- Biến contract đặt tên `hrc`, không đặt `log`, vì `Test` của forge-std đã có event tên `log`; trùng tên sẽ không biên dịch được.

---

## Phần E. Contract nói chuyện với phần còn lại thế nào

```
                          anchorLog / reportIncident  (ví DEVICE ký)
backend/chain.py  ─────────────────────────────────────────────►  HRCSafetyLog
  (web3.py)       ◄──── event EmergencyStop / Unlocked (backend đọc 1 lần/giây)
      │
      └─► gửi LOCK / UNLOCK có ký HMAC xuống robot qua MQTT

dashboard/src/chain.ts ── getRobot, đọc event ──► HRCSafetyLog   (đọc thẳng, không tin backend)
  (ethers + MetaMask)  ── unlock()  (SUPERVISOR ký trên MetaMask) ─►
```

| Hàm trong contract | Ai gọi | File gọi |
|---|---|---|
| `anchorLog` | backend, mỗi 30 s hoặc mỗi 256 bản ghi | `backend/hrc_backend/batcher.py` → `chain.py: anchor_log` |
| `reportIncident` | backend, khi AI xác nhận `danger_confirmed` | `service.py` → `chain.py: report_incident` |
| `unlock` | SUPERVISOR, trên dashboard | `dashboard/src/chain.ts` (MetaMask) |
| `getRobot`, `getBatch`, event | backend và dashboard | `chain.py: robot_info, get_batch, get_events`; `chain.ts` |
| `verifyRecord`, `leafHash` | công cụ auditor | `backend/hrc_backend/verify.py`, `scripts/verify_log.py` |
| `recordAudit` | AUDITOR | `scripts/verify_log.py --record-audit`, `tamper_demo.py` |

**Quy tắc khi sửa contract:** đổi tên hàm, thứ tự tham số hay event là phải báo **người 4** (sửa `chain.py`) và **người 5** (sửa `chain.ts`), rồi cập nhật `docs/INTERFACES.md`. Sau đó chạy lại:
```bat
npx hardhat compile
npx hardhat test
cd .. && python -m pytest tests/test_merkle.py tests/test_backend_chain.py
```

---

## Phần F. Câu hỏi phản biện thường gặp

**1. Sao không lưu toàn bộ log lên blockchain cho chắc?**
Vì quá đắt và quá chậm: khoảng 24 ETH cho một ca 8 giờ, và cần 10 giao dịch mỗi giây trong khi Sepolia 12 giây mới có 1 khối. Merkle root cho **cùng mức bảo đảm chống sửa**: sửa 1 bit là root lệch. Chi phí rẻ hơn khoảng 256 lần.

**2. Admin là người deploy, vậy admin có tự mở khóa robot được không?**
Không trực tiếp: `unlock` đòi `SUPERVISOR_ROLE`, và admin không có vai trò này. Admin *có thể* tự cấp cho mình SUPERVISOR_ROLE, nhưng việc cấp quyền phát event `RoleGranted` công khai trên chuỗi, **không che giấu được**. Hướng cải tiến: dùng ví multisig (nhiều người cùng ký) cho admin.

**3. Backend bị hack thì sao?**
Kẻ tấn công lấy được ví DEVICE thì có thể **khóa** robot (gây phiền), hoặc neo log rác, nhưng **không thể mở khóa** và **không sửa được batch đã neo**, vì batchId chỉ tăng. Admin gọi `setRobotDevice` để đổi sang ví mới, hoặc `revokeRole` thu hồi quyền ví cũ.

**4. Nếu backend không gọi `reportIncident` (bị tắt hoặc bị sửa code)?**
Robot **vẫn dừng**, vì ESP32 tự ngắt relay. Chỉ là sự cố không được ghi lên chuỗi. Nhưng log vẫn được neo, và auditor đối soát sẽ thấy bản ghi DANGER trong batch mà không có sự cố tương ứng. Thêm nữa, đây là **oracle problem**: blockchain chỉ bảo đảm dữ liệu *sau khi ghi* không bị sửa, không bảo đảm nguồn dữ liệu trung thực.

**5. Vì sao dùng custom error thay vì `require` với thông báo chữ?**
Custom error rẻ hơn (chỉ lưu 4 byte mã thay vì cả chuỗi chữ trong bytecode) và **mang tham số**, ví dụ `InvalidBatchId(expected=3, provided=2)`, nên backend và dashboard hiển thị lỗi rõ ràng.

**6. Vì sao có `verifyRecord` khi đã có `verifyLeaf`?**
Vì `verifyLeaf` nhận hash thô nên có thể bị lừa bằng node trung gian (second-preimage). `verifyRecord` tự tính leaf từ dữ liệu gốc 224 byte nên không bị lừa. Test `SECOND-PREIMAGE` chứng minh điều này.

**7. `private` thì dữ liệu có bí mật không?**
Không. Mọi storage trên blockchain đều công khai. `private` chỉ ngăn contract khác gọi trực tiếp. Vì thế contract **không chứa dữ liệu cá nhân**, chỉ chứa hash và địa chỉ ví.

**8. Contract có vòng lặp không? Tại sao quan trọng?**
Không có. Vòng lặp chạy theo số phần tử có thể làm gas vượt giới hạn khối, và **hàm không bao giờ chạy được nữa** (tấn công DoS). Mọi hàm của `HRCSafetyLog` có gas gần như cố định; riêng hàm verify phụ thuộc độ dài proof, khoảng 8 hash, và là hàm view miễn phí.

**9. Vì sao chọn OpenZeppelin, không tự viết phân quyền?**
Thư viện đã được kiểm toán và được hàng nghìn dự án dùng. Lỗi phân quyền là nguyên nhân của nhiều vụ mất tiền lớn nhất trong lịch sử smart contract.

**10. Đã deploy rồi mà phát hiện lỗi thì làm sao?**
Contract không sửa được. Phải deploy contract mới, cập nhật địa chỉ (script `deploy_sepolia.py` tự ghi vào `.env.sepolia`), và backend tự cất database cũ sang file lưu trữ. Đây là lý do phải có 50 test và coverage 100% **trước khi** deploy. Hướng nâng cao là **proxy pattern** (contract nâng cấp được), nhưng như vậy lại làm giảm tính bất biến, nên nhóm cố ý không dùng.

---

## Tóm tắt lệnh của phần bạn

```bat
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat compile                          :: biên dịch
npx hardhat test                             :: 50 test
npx hardhat test mocha test/GasReport.ts     :: bảng gas
npx hardhat test --coverage                  :: độ phủ
npx hardhat node                             :: (cửa sổ riêng) chuỗi local
npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost --reset

cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python scripts\thuc_hanh_contract.py         :: tự tay gọi từng hàm
python scripts\deploy_sepolia.py --verify    :: testnet (khi đã có .env.sepolia)
```
