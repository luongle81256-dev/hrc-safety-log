// Làm việc với blockchain bằng ethers v6:
//  - ĐỌC trực tiếp từ chuỗi (JsonRpcProvider) – lịch sử sự cố không phụ thuộc backend
//  - GHI qua MetaMask (BrowserProvider): SUPERVISOR ký unlock()
import {
  BrowserProvider,
  Contract,
  type Eip1193Provider,
  JsonRpcProvider,
  type EventLog,
  ZeroAddress,
  ZeroHash,
  getAddress,
  id,
} from "ethers";

import type { AppConfig } from "./types";

declare global {
  interface Window {
    ethereum?: Eip1193Provider & {
      on?: (ev: string, fn: (...a: unknown[]) => void) => void;
      removeListener?: (ev: string, fn: (...a: unknown[]) => void) => void;
    };
  }
}

export const ROLES = {
  ADMIN: ZeroHash,
  SUPERVISOR: id("SUPERVISOR_ROLE"),
  OPERATOR: id("OPERATOR_ROLE"),
  AUDITOR: id("AUDITOR_ROLE"),
  DEVICE: id("DEVICE_ROLE"),
} as const;
export type RoleName = keyof typeof ROLES;

export function readContract(cfg: AppConfig): Contract {
  const provider = new JsonRpcProvider(cfg.rpc_url, cfg.chain_id, { staticNetwork: true, polling: true });
  return new Contract(cfg.contract_address, cfg.abi, provider);
}

// ---------------- link Etherscan ----------------
export const txUrl = (cfg: AppConfig, hash: string) => (cfg.explorer ? `${cfg.explorer}/tx/${hash}` : null);
export const addrUrl = (cfg: AppConfig, addr: string) => (cfg.explorer ? `${cfg.explorer}/address/${addr}` : null);
export const short = (s: string, n = 6) => (s.length > 2 * n + 2 ? `${s.slice(0, n + 2)}…${s.slice(-n)}` : s);

// ---------------- đọc chuỗi ----------------
export interface OnchainRobot {
  state: number;
  currentOperator: string;
  lastBatchId: number;
  violations: number;
}

export async function readRobot(c: Contract, robotId: number): Promise<OnchainRobot> {
  const r = await c.getRobot(robotId);
  const op: string = r.currentOperator;
  const violations = op === ZeroAddress ? 0 : Number(await c.violationCount(op));
  return { state: Number(r.state), currentOperator: op, lastBatchId: Number(r.lastBatchId), violations };
}

export interface ChainRecord {
  key: string;
  event: "EmergencyStop" | "Unlocked" | "OperatorBlocked";
  robotId?: number;
  incidentId?: number;
  severity?: number;
  operator?: string;
  supervisor?: string;
  evidence?: string;
  violations?: number;
  timestamp?: number; // giây (từ tham số event)
  block: number;
  tx: string;
}

/** Lịch sử sự cố đọc THẲNG từ event on-chain (mới nhất trước). */
export async function loadHistory(c: Contract, fromBlock: number): Promise<ChainRecord[]> {
  const [stops, unlocks, blocked] = await Promise.all([
    c.queryFilter(c.filters.EmergencyStop(), fromBlock),
    c.queryFilter(c.filters.Unlocked(), fromBlock),
    c.queryFilter(c.filters.OperatorBlocked(), fromBlock),
  ]);
  const base = (e: EventLog) => ({ key: `${e.transactionHash}:${e.index}`, block: e.blockNumber, tx: e.transactionHash });
  const out: (ChainRecord & { idx: number })[] = [];
  for (const e of stops as EventLog[])
    out.push({
      ...base(e), idx: e.index, event: "EmergencyStop", robotId: Number(e.args.robotId), incidentId: Number(e.args.incidentId),
      severity: Number(e.args.severity), operator: e.args.operator, evidence: e.args.evidenceHash, timestamp: Number(e.args.timestamp),
    });
  for (const e of unlocks as EventLog[])
    out.push({
      ...base(e), idx: e.index, event: "Unlocked", robotId: Number(e.args.robotId), supervisor: e.args.supervisor,
      timestamp: Number(e.args.timestamp),
    });
  for (const e of blocked as EventLog[])
    out.push({ ...base(e), idx: e.index, event: "OperatorBlocked", operator: e.args.operator, violations: Number(e.args.violations) });
  return out.sort((a, b) => b.block - a.block || b.idx - a.idx);
}

// ---------------- ví MetaMask ----------------
export interface Wallet {
  provider: BrowserProvider;
  address: string;
  roles: Record<RoleName, boolean>;
}

async function ensureNetwork(cfg: AppConfig): Promise<void> {
  const eth = window.ethereum!;
  const want = "0x" + cfg.chain_id.toString(16);
  if ((await eth.request({ method: "eth_chainId" })) === want) return;
  try {
    await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: want }] });
  } catch (e) {
    // 4902: ví chưa biết mạng này => thêm mạng Hardhat local
    if ((e as { code?: number }).code === 4902 && cfg.chain_id === 31337) {
      await eth.request({
        method: "wallet_addEthereumChain",
        params: [{ chainId: want, chainName: "Hardhat local", rpcUrls: [cfg.rpc_url],
                   nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 } }],
      });
    } else throw e;
  }
}

export async function connectWallet(cfg: AppConfig, reader: Contract): Promise<Wallet> {
  if (!window.ethereum) throw new Error("Không tìm thấy MetaMask – hãy cài extension MetaMask");
  const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
  await ensureNetwork(cfg);
  const address = getAddress(accounts[0]);
  const entries = await Promise.all(
    (Object.keys(ROLES) as RoleName[]).map(async (r) => [r, Boolean(await reader.hasRole(ROLES[r], address))] as const),
  );
  // Tạo provider SAU khi đổi mạng (ethers ghi nhớ mạng lúc khởi tạo)
  return { provider: new BrowserProvider(window.ethereum), address, roles: Object.fromEntries(entries) as Wallet["roles"] };
}

/** SUPERVISOR mở khóa robot: mô phỏng trước (báo lỗi rõ ràng) -> ký bằng MetaMask -> chờ xác nhận. */
export async function unlockRobot(w: Wallet, cfg: AppConfig, robotId: number, onSent: (hash: string) => void) {
  const signer = await w.provider.getSigner();
  const c = new Contract(cfg.contract_address, cfg.abi, signer);
  await c.unlock.staticCall(robotId);
  const tx = await c.unlock(robotId);
  onSent(tx.hash);
  const receipt = await tx.wait();
  return { hash: tx.hash as string, block: receipt!.blockNumber, gasUsed: receipt!.gasUsed.toString() };
}

const ERROR_TEXT: Record<string, string> = {
  RobotNotLocked: "Robot không ở trạng thái LOCKED",
  AccessControlUnauthorizedAccount: "Ví này không có quyền SUPERVISOR",
  RobotNotRegistered: "Robot chưa đăng ký trên contract",
};

export function describeError(e: unknown): string {
  const err = e as { code?: string; revert?: { name: string; args: unknown[] }; shortMessage?: string; message?: string };
  if (err.code === "ACTION_REJECTED") return "Bạn đã từ chối ký trong MetaMask";
  if (err.revert?.name) return `${ERROR_TEXT[err.revert.name] ?? "Contract từ chối"} (${err.revert.name})`;
  return err.shortMessage ?? err.message ?? String(e);
}
