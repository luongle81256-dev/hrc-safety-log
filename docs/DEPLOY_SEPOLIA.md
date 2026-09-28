# Deploy HRCSafetyLog lên Sepolia (từ đầu đến cuối)

Hướng dẫn cho người phụ trách smart contract. Dùng **Command Prompt (cmd)**. Làm lần lượt từ bước 1 đến bước 12, **đừng bỏ bước**.

**Thời gian:** khoảng 30–60 phút cho lần đầu. Phần lâu nhất là xin ETH thử nghiệm.

**Chi phí:** **0 đồng**. Sepolia là **testnet**, ETH trên đó không có giá trị, được phát miễn phí.

---

## 0. Hiểu trước khi làm

**Sepolia là gì?** Một mạng Ethereum **thật**: có hàng nghìn máy chạy, và ai cũng tra cứu được qua Etherscan. Điểm khác duy nhất là ETH trên đó miễn phí. So với chuỗi local (`npx hardhat node`):

| | Chuỗi local | Sepolia |
|---|---|---|
| Ở đâu | chỉ trên máy bạn, tắt là mất | Internet, tồn tại lâu dài |
| Ai kiểm tra được | chỉ bạn | **cả thế giới**, qua sepolia.etherscan.io |
| Tạo khối | tức thì | khoảng **12 giây** một khối |
| ETH | 10.000 ETH giả có sẵn | phải xin từ **faucet** |

**Vì sao đề bài yêu cầu Sepolia?** Để chứng minh tính **minh bạch và bất biến** là thật. Thầy hoặc bất kỳ ai mở link Etherscan đều thấy contract, mã nguồn, mọi sự cố và mọi lần mở khóa, mà nhóm **không sửa được**.

**Bạn sẽ cần 4 thứ:**

| Thứ cần | Để làm gì | Lấy ở bước |
|---|---|---|
| Ví MetaMask test, 2 tài khoản | ký giao dịch deploy; ký mở khóa | 1 |
| SepoliaETH, khoảng 0,02–0,05 | trả phí gas | 2 |
| RPC URL (Alchemy) | "cổng" để phần mềm gửi giao dịch vào Sepolia | 3 |
| Etherscan API key | công bố mã nguồn contract (verify) | 4 |

**Phân vai ví trên Sepolia:**

| Vai trò | Ví | Lý do |
|---|---|---|
| ADMIN + DEVICE (backend ghi log) + OPERATOR + AUDITOR | **Account 1** (ví deploy) | đơn giản cho demo, chỉ 1 ví cần ETH |
| **SUPERVISOR** (mở khóa) | **Account 2** | tách riêng người mở khóa, đúng tinh thần đồ án: backend khóa được nhưng không tự mở được |

> ⚠️ **Quy tắc an toàn**
> - Chỉ dùng ví **tạo mới riêng cho đồ án**, không bao giờ dùng ví có tiền thật.
> - **Không gửi private key** qua chat, Zalo, Messenger, kể cả cho thành viên nhóm.
> - File `.env.sepolia` chứa private key và **đã được Git bỏ qua**. Không copy nội dung của nó đi đâu.

---

## Bước 1. Tạo ví MetaMask test

1. Cài extension **MetaMask** cho Chrome hoặc Edge, tải từ trang chính thức metamask.io.
   - Nếu đã có MetaMask với ví thật, **tạo profile trình duyệt riêng** cho đồ án.
2. Chọn *Create a new wallet*, đặt mật khẩu, và **chép cụm 12 từ khôi phục ra giấy**.
3. Bật mạng thử nghiệm: *Settings → Advanced → Show test networks*, gạt sang **On**.
4. Ở ô chọn mạng (góc trên), chọn **Sepolia**.
5. Tạo tài khoản thứ 2: bấm tên tài khoản → *Add account* (hoặc *Create account*). Bạn có **Account 1** và **Account 2**.
6. Ghi lại **địa chỉ** của cả 2 tài khoản (dạng `0x…`, bấm vào tên tài khoản là copy được).

✅ **Kết quả:** MetaMask đang ở mạng Sepolia, có 2 tài khoản với số dư 0 SepoliaETH.

> Giao diện MetaMask thay đổi theo phiên bản. Nếu không thấy đúng tên nút, tìm mục có ý nghĩa tương tự.

---

## Bước 2. Xin SepoliaETH (faucet)

**Vì sao:** mọi giao dịch đều tốn gas. Deploy và cấp quyền tốn khoảng 2 triệu gas, tức **khoảng 0,002 ETH** với giá gas hiện tại (con số này script tự tính ở bước 8).

1. Copy địa chỉ **Account 1**.
2. Vào một faucet Sepolia, dán địa chỉ, rồi bấm nhận. Một số faucet phổ biến:
   - Google Cloud Web3 Faucet: `cloud.google.com/application/web3/faucet/ethereum/sepolia`
   - Alchemy Faucet: `alchemy.com/faucets/ethereum-sepolia`
   - Sepolia PoW Faucet: `sepolia-faucet.pk910.de` (phải "đào" vài phút trên trình duyệt)
3. Chờ 1–5 phút, rồi kiểm tra số dư trong MetaMask.

> Faucet **thay đổi điều kiện thường xuyên**: có cái đòi đăng nhập, có cái đòi ví phải có sẵn một ít ETH thật trên mainnet. Faucet này không được thì thử faucet khác. Các thành viên có thể xin rồi **chuyển gộp** vào Account 1.

**Cần bao nhiêu:**
- Tối thiểu khoảng **0,01 ETH** để deploy.
- Nên có **0,05 ETH** để còn chạy demo, vì backend neo log mỗi 30 giây, mỗi lần khoảng 85.000 gas.

Account 2 **cũng cần một ít** (khoảng 0,005 ETH) để trả phí khi bấm Mở khóa. Chuyển từ Account 1 sang: *Send → chọn Account 2 → 0.005*.

✅ **Kết quả:** Account 1 có ≥ 0,01 SepoliaETH; Account 2 có khoảng 0,005.

---

## Bước 3. Lấy RPC URL (Alchemy)

**Vì sao:** muốn gửi giao dịch vào Sepolia, phần mềm cần kết nối tới một máy đang chạy node Sepolia. Alchemy cho dùng node của họ **miễn phí**, qua một đường link riêng gọi là **RPC URL**.

1. Vào **alchemy.com** → *Sign up* (đăng ký bằng Google cho nhanh).
2. Tạo app: *Create new app*, đặt tên `hrc-safety-log`, chọn chain **Ethereum**, mạng **Sepolia**.
3. Mở app → *Endpoints* (hoặc *API key*) → copy dòng **HTTPS**, có dạng:
   ```
   https://eth-sepolia.g.alchemy.com/v2/AbCdEf123...
   ```

> Phần cuối link là **API key riêng** của bạn: cũng là bí mật, đừng công khai. Có thể dùng Infura (`https://sepolia.infura.io/v3/<key>`) thay Alchemy.

✅ **Kết quả:** có một link `https://eth-sepolia.g.alchemy.com/v2/…`.

---

## Bước 4. Lấy Etherscan API key

**Vì sao:** "verify" là gửi **mã nguồn Solidity** lên Etherscan. Etherscan tự biên dịch lại, so với bytecode trên chuỗi, và nếu khớp thì hiện dấu ✓ kèm toàn bộ code cho mọi người đọc. Không verify thì người khác chỉ thấy một dãy bytecode khó hiểu.

1. Vào **etherscan.io** → *Sign in → Click to sign up*, tạo tài khoản và xác nhận email.
2. Vào menu tài khoản → **API Keys** → *Add*, đặt tên `hrc`.
3. Copy chuỗi key, khoảng 34 ký tự.

> Key tạo ở etherscan.io dùng được cho cả Sepolia.

✅ **Kết quả:** có một chuỗi API key.

---

## Bước 5. Lấy private key của Account 1

**Vì sao:** script deploy chạy trên máy, không bấm MetaMask được, nên cần private key để tự ký giao dịch.

1. Trong MetaMask, chọn **Account 1** → nút **⋮** → *Account details* → *Show private key* (hoặc *Details → Show private key*).
2. Nhập mật khẩu MetaMask, giữ nút xác nhận, rồi copy.
3. Private key là chuỗi 64 ký tự hex. **Nếu không bắt đầu bằng `0x`, tự thêm `0x` vào đầu.**

> ⚠️ Ai có chuỗi này là **toàn quyền** với ví. Chỉ dán vào file `.env.sepolia` ở bước 6, không dán vào đâu khác.

**Không cần** private key của Account 2. Account 2 ký trực tiếp bằng MetaMask trên dashboard.

---

## Bước 6. Tạo file `.env.sepolia`

**Vì sao:** mọi thông tin mạng Sepolia nằm trong **một file riêng**, tách hẳn với `.env` của chuỗi local. Khi chạy với `HRC_NETWORK=sepolia`, chương trình đọc file này **trước**; cấu hình local không bị ảnh hưởng.

Mở cmd:

```bat
cd /d D:\Blockchain\Report_CK
copy .env.sepolia.example .env.sepolia
notepad .env.sepolia
```

Trong Notepad, **chỉ điền giá trị sau dấu `=`** cho 4 dòng dưới đây. **Không xóa dòng nào khác**, kể cả các dòng đang để trống:

```ini
SEPOLIA_RPC_URL=https://eth-sepolia.g.alchemy.com/v2/<key-bước-3>
SEPOLIA_PRIVATE_KEY=0x<private-key-Account-1-bước-5>
ETHERSCAN_API_KEY=<key-bước-4>
SUPERVISOR_ADDRESS=0x<địa-chỉ-Account-2>
```

Lưu (`Ctrl+S`) rồi đóng Notepad.

**Ý nghĩa các dòng quan trọng trong file:**

| Dòng | Ý nghĩa |
|---|---|
| `SEPOLIA_RPC_URL` | cổng kết nối Sepolia |
| `SEPOLIA_PRIVATE_KEY` | ví deploy, nhận DEFAULT_ADMIN |
| `DEVICE_PRIVATE_KEY=${SEPOLIA_PRIVATE_KEY}` | backend dùng **chính ví deploy** để ghi log. Để nguyên |
| `SUPERVISOR_ADDRESS` | người được mở khóa = Account 2 |
| `OPERATOR_ADDRESS=`, `AUDITOR_ADDRESS=` | để trống thì dùng ví deploy |
| `OPERATOR_PRIVATE_KEY=` … (để trống) | **phải giữ dòng này**, dù trống |
| `CONTRACT_ADDRESS=`, `CHAIN_START_BLOCK=` | để trống; **script tự điền** sau khi deploy |
| `SQLITE_PATH=…hrc_log_sepolia.sqlite3` | log Sepolia lưu riêng, không trộn với log local |

> **Vì sao không được xóa các dòng trống?** Dòng trống nghĩa là "trên Sepolia không có giá trị này". Nếu xóa hẳn dòng, chương trình sẽ lấy giá trị cùng tên trong `.env`, tức khóa Hardhat local. Các khóa đó **công khai trên Internet**, ai cũng có. Script deploy sẽ phát hiện và **từ chối deploy** nếu gặp trường hợp này.

**Kiểm tra file không bị đưa lên GitHub:**

```bat
git check-ignore -v .env.sepolia
```

✅ Đúng khi in ra dòng có chữ `.gitignore` và `.env.*`. Nếu **không in gì**, dừng lại và báo nhóm.

---

## Bước 7. Kiểm tra contract lần cuối trước khi deploy

**Vì sao:** contract đã lên Sepolia thì **không sửa được**. Lỗi phát hiện sau khi deploy nghĩa là phải deploy lại, tốn ETH, và link Etherscan cũ bỏ đi.

```bat
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat test
```

✅ Đúng khi thấy **50 passing**. Còn test đỏ thì **không deploy**.

### (Nên làm) Tập dượt deploy trên chuỗi local

**Vì sao:** chạy **đúng quy trình deploy Sepolia** (cùng script, cùng kiểu cấu hình 1 ví) nhưng trên chuỗi local, không tốn ETH. Lỗi gì sẽ lộ ra ở đây.

Cửa sổ cmd 1:
```bat
cd /d D:\Blockchain\Report_CK\contracts
npx hardhat node
```

Cửa sổ cmd 2:
```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python scripts\deploy_sepolia.py --dry-run-local
```

✅ Đúng khi dòng cuối là `[OK] Contract 0x… tại block … – vai trò + robot 1 đúng trên chuỗi`.

Xong thì tắt cửa sổ 1 (`Ctrl+C`). Lần tập dượt này thay contract trên chuỗi local; lần sau chạy demo local, nhớ deploy lại như thường lệ.

---

## Bước 8. Deploy thật lên Sepolia

Trong cửa sổ cmd đã kích hoạt `.venv`:

```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
python scripts\deploy_sepolia.py --verify
```

Script lần lượt làm và in ra như sau.

**① Kiểm tra trước khi tiêu ETH:**

```
Mạng: chain 11155111 (Sepolia)
Ví deploy (DEFAULT_ADMIN): 0xAbc…(Account 1) | số dư 0.05000 ETH | ước tính phí ~0.00197 ETH
  DEVICE     -> 0xAbc…  (= ví deploy)
  SUPERVISOR -> 0xDef…(Account 2)
  OPERATOR   -> 0xAbc…  (= ví deploy)
  AUDITOR    -> 0xAbc…  (= ví deploy)
Deploy lên Sepolia (tiêu SepoliaETH)? Gõ 'yes' để tiếp tục:
```

**Đọc kỹ trước khi gõ `yes`:**
- `chain 11155111`: đúng mạng Sepolia.
- Số dư lớn hơn phí ước tính.
- `SUPERVISOR` là **địa chỉ Account 2**, không phải ví deploy.

Đúng hết thì gõ `yes` rồi Enter.

**② Ignition deploy**, mất khoảng 1–3 phút vì mỗi khối Sepolia khoảng 12 giây:

```
Batch #1
  Executed HRCSafetyLogModule#HRCSafetyLog
Batch #2
  Executed HRCSafetyLogModule#grantAuditor
  Executed HRCSafetyLogModule#grantDevice
  Executed HRCSafetyLogModule#grantOperator
  Executed HRCSafetyLogModule#grantSupervisor
  Executed HRCSafetyLogModule#registerRobot_1
[ HRCSafetyLogModule ] successfully deployed 🚀
```

Thứ tự này do Ignition tự sắp: phải có contract rồi mới cấp quyền được. 5 lệnh ở Batch #2 độc lập với nhau nên được gửi cùng lúc.

**③ Verify mã nguồn:** thấy dòng báo đã verify thành công trên Etherscan, kèm link.

**④ Tự kiểm tra kết quả trên chuỗi:**

```
[OK] Contract 0x1234… tại block 7xxxxxx – vai trò + robot 1 đúng trên chuỗi
     Đã ghi CONTRACT_ADDRESS + CHAIN_START_BLOCK vào .env.sepolia
     Etherscan: https://sepolia.etherscan.io/address/0x1234…
```

Script **đọc lại từ chuỗi** để chắc 5 vai trò đã cấp đúng người và robot 1 đã đăng ký, rồi tự ghi địa chỉ contract vào `.env.sepolia`, nên bạn không phải chép tay.

✅ **Kết quả:** có link Etherscan. **Lưu link này để đưa vào báo cáo.**

### Nếu có sự cố giữa chừng

| Tình huống | Làm gì |
|---|---|
| Mất mạng hoặc tắt máy giữa lúc deploy | Chạy **lại đúng lệnh cũ**. Ignition nhớ bước nào đã xong và **làm tiếp**, không deploy trùng |
| Deploy xong nhưng verify báo lỗi | Chờ 1–2 phút rồi chạy lại `python scripts\deploy_sepolia.py --verify`. Script thấy contract đã có nên **chỉ verify lại**, không tốn thêm ETH |
| Muốn deploy một contract **mới hoàn toàn** (vì đã sửa code) | `python scripts\deploy_sepolia.py --verify --redeploy`. Contract cũ vẫn nằm trên chuỗi mãi mãi, chỉ là không dùng nữa |

---

## Bước 9. Kiểm tra trên Etherscan

Mở link Etherscan ở bước 8.

| Tab | Thấy gì | Ý nghĩa |
|---|---|---|
| **Transactions** | 1 giao dịch *Contract Creation* + 5 giao dịch `Grant Role` / `Register Robot` | đúng các bước Ignition đã làm |
| **Contract** (có dấu ✓) | toàn bộ mã `HRCSafetyLog.sol` | ai cũng đọc và kiểm được contract làm gì |
| **Contract → Read Contract** | gọi thử `getRobot(1)`, `robotState(1)`, `MAX_VIOLATIONS` | đọc dữ liệu trực tiếp, không cần phần mềm của nhóm |
| **Events** | `RoleGranted`, `RobotRegistered` | nhật ký bất biến |

Thử: *Read Contract → `hasRole`* với `role` = giá trị của `SUPERVISOR_ROLE` (lấy ở cùng trang) và `account` = địa chỉ Account 2. Kết quả phải là **true**.

---

## Bước 10. Chạy toàn hệ thống trên Sepolia

Giống demo local, **khác 3 điểm**:
1. **Không** chạy `npx hardhat node`.
2. **Không** deploy.
3. Đặt `set HRC_NETWORK=sepolia` trước lệnh backend.

**Cửa sổ 1: backend**
```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
set HRC_NETWORK=sepolia
hrc-backend
```

✅ Đúng khi thấy `Mạng sepolia (chain 11155111), contract 0x1234…`.

> Trên Sepolia **không truyền `--batch-interval-ms 8000`**. Để mặc định 30 giây một batch để đỡ tốn ETH.

**Cửa sổ 2: dashboard**
```bat
cd /d D:\Blockchain\Report_CK\dashboard
npm run dev
```

Mở http://localhost:5173. Dashboard tự nhận ra đang ở Sepolia (thông tin lấy từ backend) và hiện **link Etherscan** cho mọi giao dịch.

**Cửa sổ 3: robot giả lập**
```bat
cd /d D:\Blockchain\Report_CK
.venv\Scripts\activate.bat
hrc-sim --scenario approach --duration 60
```

**Khác với local:**
- Robot vẫn dừng **ngay lập tức**, vì fast lane ở ESP32 không phụ thuộc chuỗi.
- Sự cố xuất hiện trên chuỗi sau khoảng **15–30 giây**, vì phải chờ Sepolia tạo khối.

**Mở khóa bằng MetaMask:**
1. MetaMask chọn **Account 2** và mạng **Sepolia**.
2. Trên dashboard bấm **Kết nối MetaMask**. Phải thấy huy hiệu **SUPERVISOR**.
3. Chờ robot về vùng SAFE: chạy `hrc-sim --scenario normal --duration 300` ở cửa sổ 3.
4. Bấm **Mở khóa (ký bằng MetaMask)** → **Xác nhận** trong MetaMask.
5. Khoảng 15–30 giây sau, robot về **ACTIVE**. Bấm link Etherscan của giao dịch `unlock`: ô *From* chính là Account 2, **bằng chứng ai đã mở khóa**.

**Kịch bản demo có lời dẫn** (thay cho cửa sổ 3):
```bat
set HRC_NETWORK=sepolia
python scripts\demo_scenario.py --pause
```

**Đối soát log với chuỗi:**
```bat
set HRC_NETWORK=sepolia
python scripts\verify_log.py
python scripts\tamper_demo.py
```

---

## Bước 11. Tiết kiệm SepoliaETH

Backend chạy là **tốn gas liên tục**: mỗi 30 giây neo 1 batch, khoảng 85.000 gas mỗi lần. Ở giá gas khoảng 1 gwei, **1 giờ chạy tốn khoảng 0,01 ETH**; giá gas cao hơn thì tốn nhiều hơn.

- **Tắt backend (`Ctrl+C`) khi không demo.**
- Trước buổi bảo vệ, kiểm tra số dư Account 1 trong MetaMask. Còn dưới 0,01 thì xin thêm faucet.
- Hết ETH giữa chừng thì backend báo lỗi gửi giao dịch. **Robot vẫn an toàn**, chỉ là log không được neo, và sẽ neo tiếp khi có ETH trở lại.

---

## Bước 12. Đưa vào báo cáo

1. Link contract: `https://sepolia.etherscan.io/address/0x…`, có dấu ✓ verified.
2. Ảnh chụp tab **Contract** (mã nguồn đã verify).
3. Link một giao dịch `reportIncident` và một giao dịch `unlock` (lấy từ dashboard).
4. Bảng gas thực tế: tab *Transactions* trên Etherscan có cột phí từng giao dịch; so với bảng gas đo ở local.
5. Độ trễ ghi sự cố trên Sepolia (khoảng 15–30 s), và giải thích vì sao **không nguy hiểm**: fast lane đã dừng robot trước đó.

---

## Lỗi thường gặp

| Thông báo | Nguyên nhân / cách xử lý |
|---|---|
| `HRC_NETWORK=sepolia nhưng thiếu .env.sepolia` | Chưa làm bước 6, hoặc file bị đặt tên sai (Notepad có thể lưu thành `.env.sepolia.txt`). Kiểm tra bằng `dir /a .env*` |
| `Thiếu SEPOLIA_RPC_URL hoặc SEPOLIA_PRIVATE_KEY` | Chưa điền, hoặc điền dư dấu cách hay dấu ngoặc kép. Viết liền: `SEPOLIA_RPC_URL=https://…` |
| `Không kết nối được RPC` | Link Alchemy sai, hoặc app Alchemy không phải mạng Sepolia |
| `RPC đang trỏ chain 1, không phải Sepolia` | Đang dùng link **Mainnet**. Lấy lại link có chữ `eth-sepolia` |
| `Số dư không đủ phí ước tính` | Xin thêm faucet (bước 2) vào **Account 1** |
| `Vai trò … đang là ví Hardhat mặc định` | Đã xóa mất dòng `*_PRIVATE_KEY=` trong `.env.sepolia`. Copy lại từ `.env.sepolia.example` và điền lại |
| `[ĐÃ CÓ] Contract đã deploy` | Đã deploy rồi, không cần làm lại. Muốn bản mới thì thêm `--redeploy` |
| Private key báo không hợp lệ | Thiếu `0x` ở đầu, hoặc copy thiếu ký tự (phải đủ 64 ký tự sau `0x`) |
| Verify báo lỗi ngay sau deploy | Etherscan chưa kịp thấy contract. Chờ 1–2 phút rồi chạy lại lệnh `--verify` |
| MetaMask: nút Mở khóa mờ | Đang chọn Account 1. Chuyển sang **Account 2** (SUPERVISOR) rồi kết nối lại |
| MetaMask: `insufficient funds` khi mở khóa | Account 2 hết ETH. Chuyển thêm từ Account 1 (bước 2) |

---

## Checklist

- [ ] Ví MetaMask **mới**, 2 tài khoản, đang ở mạng Sepolia
- [ ] Account 1 có ≥ 0,01 SepoliaETH; Account 2 có khoảng 0,005
- [ ] `.env.sepolia` đã điền 4 dòng, **không xóa dòng nào**
- [ ] `git check-ignore -v .env.sepolia` có in ra dòng `.gitignore`
- [ ] `npx hardhat test` → 50 passing
- [ ] `deploy_sepolia.py --dry-run-local` → `[OK]`
- [ ] `deploy_sepolia.py --verify` → `[OK]` + link Etherscan có dấu ✓
- [ ] Chạy thử 1 lượt: sự cố → LOCKED → Account 2 mở khóa trên MetaMask → ACTIVE
- [ ] Lưu link Etherscan và ảnh chụp vào báo cáo
