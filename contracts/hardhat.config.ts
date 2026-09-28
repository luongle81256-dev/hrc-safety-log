import hardhatToolboxMochaEthersPlugin from "@nomicfoundation/hardhat-toolbox-mocha-ethers";
import { configVariable, defineConfig } from "hardhat/config";

// Cùng 1 cấu hình compiler cho test và deploy để số gas đo được = số gas thực tế
const solcSettings = {
  version: "0.8.28",
  settings: {
    optimizer: { enabled: true, runs: 200 },
  },
};

export default defineConfig({
  plugins: [hardhatToolboxMochaEthersPlugin],
  solidity: {
    profiles: {
      default: solcSettings,
      production: solcSettings,
    },
  },
  networks: {
    // Chuỗi mô phỏng trong tiến trình (dùng cho `hardhat test`)
    hardhatMainnet: {
      type: "edr-simulated",
      chainType: "l1",
    },
    // Node local chạy bằng `npx hardhat node` (Phase 4: backend kết nối vào đây)
    localhost: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8545",
    },
    // Sepolia (Phase 8). Biến cấu hình chỉ được đọc khi dùng mạng này:
    //   đặt qua biến môi trường hoặc `npx hardhat keystore set SEPOLIA_PRIVATE_KEY`
    sepolia: {
      type: "http",
      chainType: "l1",
      url: configVariable("SEPOLIA_RPC_URL"),
      accounts: [configVariable("SEPOLIA_PRIVATE_KEY")],
    },
  },
  // Verify mã nguồn lên Etherscan (Phase 8): chỉ đọc ETHERSCAN_API_KEY khi chạy với --verify
  verify: {
    etherscan: {
      apiKey: configVariable("ETHERSCAN_API_KEY"),
    },
  },
});
