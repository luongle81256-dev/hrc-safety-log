// Deploy HRCSafetyLog + cấp role + đăng ký robot.
//
// Local (Hardhat node): không cần tham số – mặc định là các tài khoản Hardhat CÔNG KHAI #1..#4
//   npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost
// Sepolia (Phase 8): dùng scripts/deploy_sepolia.py (sinh file tham số với địa chỉ thật rồi gọi lệnh):
//   npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network sepolia --parameters <file>
//
// Mặc định là ĐỊA CHỈ CỐ ĐỊNH chứ không phải m.getAccount(i): trên Sepolia chỉ có 1 tài khoản (ví deploy),
// mà Ignition vẫn kiểm tra giá trị mặc định getAccount(1..4) kể cả khi đã truyền tham số => lỗi HHE10723.
import { readFileSync } from "node:fs";
import { buildModule } from "@nomicfoundation/hardhat-ignition/modules";
import { id } from "ethers";

// Tham số dùng chung sinh từ config/hrc_config.toml (scripts/gen_config.py)
const HRC = JSON.parse(readFileSync(new URL("../../hrc_params.json", import.meta.url), "utf8"));

// Tài khoản mặc định của Hardhat node (mnemonic "test test ... junk") – CHỈ có ý nghĩa trên mạng local
const HARDHAT_DEFAULTS = {
  device: "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", // #1
  supervisor: "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC", // #2
  operator: "0x90F79bf6EB2c4f870365E785982E1f101E93b906", // #3
  auditor: "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65", // #4
};

export default buildModule("HRCSafetyLogModule", (m) => {
  // admin = ví gửi giao dịch deploy (account 0) vì các lệnh grantRole bên dưới cần quyền admin
  const admin = m.getAccount(0);
  const device = m.getParameter("device", HARDHAT_DEFAULTS.device);
  const supervisor = m.getParameter("supervisor", HARDHAT_DEFAULTS.supervisor);
  const operator = m.getParameter("operator", HARDHAT_DEFAULTS.operator);
  const auditor = m.getParameter("auditor", HARDHAT_DEFAULTS.auditor);
  const maxViolations = m.getParameter("maxViolations", HRC.maxViolations);

  const hrcSafetyLog = m.contract("HRCSafetyLog", [admin, maxViolations]);

  m.call(hrcSafetyLog, "grantRole", [id("DEVICE_ROLE"), device], { id: "grantDevice" });
  m.call(hrcSafetyLog, "grantRole", [id("SUPERVISOR_ROLE"), supervisor], { id: "grantSupervisor" });
  m.call(hrcSafetyLog, "grantRole", [id("OPERATOR_ROLE"), operator], { id: "grantOperator" });
  m.call(hrcSafetyLog, "grantRole", [id("AUDITOR_ROLE"), auditor], { id: "grantAuditor" });

  for (const robotId of HRC.robotIds as number[]) {
    m.call(hrcSafetyLog, "registerRobot", [BigInt(robotId), device], { id: `registerRobot_${robotId}` });
  }

  return { hrcSafetyLog };
});
