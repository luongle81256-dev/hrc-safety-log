// Bảng điều khiển 1 robot: zone real-time, trạng thái relay / chuỗi / thiết bị / AI, biểu đồ, nút mở khóa (MetaMask)
import type { OnchainRobot, Wallet } from "../chain";
import { addrUrl, short, txUrl } from "../chain";
import type { AppConfig, RobotState, TelemetryMsg } from "../types";
import { DistanceChart } from "./DistanceChart";
import { Status, ZONE_UI } from "./Status";

export interface UnlockUi {
  phase: "idle" | "signing" | "pending" | "done" | "error";
  hash?: string;
  error?: string;
}

const AI_TEXT: Record<string, string> = {
  NORMAL: "Bình thường",
  INTRUSION: "Người xâm nhập",
  OVERSPEED: "Robot vượt tốc",
  SENSOR_FAULT: "Lỗi cảm biến",
};

interface Props {
  cfg: AppConfig;
  robot: RobotState;
  onchain?: OnchainRobot;
  series: TelemetryMsg[];
  wallet: Wallet | null;
  unlock: UnlockUi;
  onUnlock: () => void;
}

export function RobotPanel({ cfg, robot, onchain, series, wallet, unlock, onUnlock }: Props) {
  const t = robot.last;
  const rid = robot.robot_id;
  const zone = t ? ZONE_UI[t.zone] : { tone: "neutral" as const, label: "CHƯA CÓ DỮ LIỆU" };
  // Trạng thái chuỗi: ưu tiên đọc trực tiếp từ contract, dự phòng theo event backend báo
  const chainState = onchain?.state ?? robot.chain_state;
  const locked = chainState === 1;
  const operator = onchain?.currentOperator ?? robot.current_operator;
  const hasOperator = operator && !/^0x0+$/.test(operator);
  const canUnlock = locked && wallet?.roles.SUPERVISOR && (unlock.phase === "idle" || unlock.phase === "done" || unlock.phase === "error");
  const busy = unlock.phase === "signing" || unlock.phase === "pending";
  const link = unlock.hash ? txUrl(cfg, unlock.hash) : null;

  return (
    <section className="card" aria-labelledby={`robot-${rid}-title`}>
      <h2 id={`robot-${rid}-title`}>Robot #{rid}</h2>
      <p className="sub">Fast lane (ESP32) quyết định zone và ngắt relay tại chỗ · trust lane ghi bằng chứng lên chuỗi</p>

      <div className="robot-head">
        <Status big tone={zone.tone} label={zone.label} testId={`robot-${rid}-zone`} />
        <div className="hero-num" data-testid={`robot-${rid}-distance`}>
          {t ? (t.distance_mm >= 8190 ? "—" : t.distance_mm) : "—"}
          <small>{t && t.distance_mm >= 8190 ? "ngoài tầm đo" : "mm tới người"}</small>
        </div>
        {t && <span className="hint">S = {t.s_mm} mm · cảnh báo ≤ {t.warn_mm} mm (profile {cfg.ssm.profile})</span>}
      </div>

      {locked && (
        <div className="lockbox" data-testid={`robot-${rid}-lockbox`}>
          <p>
            <strong>Robot đang bị KHÓA trên blockchain.</strong> Chỉ ví có quyền SUPERVISOR mới mở được; thiết bị
            chỉ chạy lại khi vùng làm việc SAFE đủ 1 giây.
          </p>
          <button className="btn primary" disabled={!canUnlock || busy} onClick={onUnlock} data-testid={`unlock-${rid}`}>
            {unlock.phase === "signing" ? "Đang chờ ký trong MetaMask…" : unlock.phase === "pending" ? "Đang chờ xác nhận…" : "Mở khóa (ký bằng MetaMask)"}
          </button>
          {!wallet && <span className="hint">Kết nối ví để mở khóa</span>}
          {wallet && !wallet.roles.SUPERVISOR && <span className="hint">Ví đang kết nối không có quyền SUPERVISOR</span>}
        </div>
      )}
      {unlock.phase === "error" && <p className="err" data-testid={`unlock-${rid}-error`}>{unlock.error}</p>}
      {unlock.hash && (
        <p className="hint" data-testid={`unlock-${rid}-tx`}>
          {unlock.phase === "done" ? "✓ Đã mở khóa – " : "Đã gửi – "}giao dịch{" "}
          {link ? <a href={link} target="_blank" rel="noreferrer" className="mono">{short(unlock.hash, 8)} ↗ Etherscan</a>
                : <code title={unlock.hash}>{short(unlock.hash, 8)}</code>}
          {!link && " (mạng local – không có Etherscan)"}
        </p>
      )}

      <dl className="facts">
        <div className="fact"><dt>Relay E-Stop</dt>
          <dd>{t ? <Status tone={t.estop ? "critical" : "good"} label={t.estop ? "NGẮT" : "ĐANG CHẠY"} /> : "—"}</dd></div>
        <div className="fact"><dt>Trạng thái on-chain</dt>
          <dd data-testid={`robot-${rid}-chain`} data-state={chainState ?? ""}>
            {chainState === null || chainState === undefined ? "—" : <Status tone={locked ? "critical" : "good"} label={locked ? "LOCKED" : "ACTIVE"} />}
          </dd></div>
        <div className="fact"><dt>Thiết bị</dt>
          <dd>{robot.device.online ? <Status tone="good" label="ONLINE" /> : <Status tone="neutral" label={`OFFLINE${robot.device.offline_reason ? ` (${robot.device.offline_reason})` : ""}`} />}</dd></div>
        <div className="fact"><dt>AI (RandomForest)</dt>
          <dd>{t?.ai ? `${AI_TEXT[t.ai.label]} · ${(t.ai.confidence * 100).toFixed(0)}%` : "đang gom cửa sổ…"}</dd></div>
        <div className="fact"><dt>Tốc độ tay robot</dt>
          <dd>{t ? `${t.v_robot_mmps} mm/s` : "—"}<span className="hint">(giới hạn {cfg.ssm.v_robot_limit_mmps})</span></dd></div>
        <div className="fact"><dt>Operator đang vận hành</dt>
          <dd>{hasOperator ? (
            <>
              {addrUrl(cfg, operator!) ? <a href={addrUrl(cfg, operator!)!} target="_blank" rel="noreferrer" className="mono">{short(operator!, 4)}</a>
                                        : <span className="mono">{short(operator!, 4)}</span>}
              {onchain && <span className="hint">vi phạm {onchain.violations}/{cfg.max_violations}</span>}
            </>
          ) : "không có"}</dd></div>
      </dl>

      <DistanceChart data={series} yMax={cfg.ssm.distance_valid_max_mm + 100} />
    </section>
  );
}
