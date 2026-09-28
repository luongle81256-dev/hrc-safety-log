// Kiểu dữ liệu khớp backend/hrc_backend/api.py và service.py
import type { InterfaceAbi } from "ethers";

export type Zone = 0 | 1 | 2;

export interface Ai {
  label: "NORMAL" | "INTRUSION" | "OVERSPEED" | "SENSOR_FAULT";
  confidence: number;
  anomaly: boolean;
  if_anomaly: boolean;
  ae_anomaly: boolean;
  danger_confirmed: boolean;
}

export interface TelemetryMsg {
  type: "telemetry";
  robot_id: number;
  seq: number;
  ts_ms: number;
  distance_mm: number;
  v_robot_mmps: number;
  zone: Zone;
  estop: 0 | 1;
  s_mm: number; // khoảng cách bảo vệ SSM ở tốc độ hiện tại
  warn_mm: number; // biên vùng cảnh báo
  ai: Ai | null;
}

export interface DeviceInfo {
  robot_id: number;
  online: boolean;
  state: number | null; // trạng thái khóa tại thiết bị (heartbeat)
  estop: number | null;
  cfg_match: boolean | null;
  offline_reason: string | null;
}

export interface RobotState {
  robot_id: number;
  device: DeviceInfo;
  chain_state: number | null; // 0 ACTIVE, 1 LOCKED (theo event on-chain)
  current_operator: string | null;
  last: TelemetryMsg | null;
}

export interface BackendState {
  robots: RobotState[];
  stats: Record<string, number>;
  mqtt_connected: boolean;
  chain_ready: boolean;
}

export interface BatchRow {
  robot_id: number;
  batch_id: number;
  merkle_root: string;
  from_seq: number;
  to_seq: number;
  n_records: number;
  tx_hash: string | null;
  gas_used: number | null;
}

export interface VerifyResult {
  robot_id: number;
  batch_id: number;
  n_records: number;
  root_match: boolean;
  onchain_checked: number;
  onchain_ok: number;
  hmac_bad_seqs: number[];
  ok: boolean;
}

export interface AppConfig {
  chain_id: number;
  contract_address: string;
  rpc_url: string;
  explorer: string | null;
  start_block: number;
  abi: InterfaceAbi;
  robot_ids: number[];
  ssm: { warning_margin_mm: number; v_robot_limit_mmps: number; distance_valid_max_mm: number; profile: string };
  max_violations: number;
}

export interface Activity {
  id: number;
  ts: number;
  tone: "info" | "good" | "warning" | "critical";
  text: string;
  tx?: string;
}
