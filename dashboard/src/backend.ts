// Kết nối backend: REST lấy cấu hình + WebSocket nhận dữ liệu real-time (tự nối lại khi rớt).
import { useEffect, useRef, useState } from "react";

import type { Activity, AppConfig, BackendState, BatchRow, TelemetryMsg, VerifyResult } from "./types";

const SERIES_MAX = 300; // 30 s ở 10 Hz

export type WsStatus = "connecting" | "open" | "closed";

export interface Backend {
  config: AppConfig | null;
  state: BackendState | null;
  series: Record<number, TelemetryMsg[]>;
  batches: BatchRow[];
  activity: Activity[];
  wsStatus: WsStatus;
  chainTick: number; // tăng mỗi khi có event on-chain => các view đọc chuỗi tự làm mới
}

let activityId = 0;
const SEVERITY = ["", "cảnh báo", "nguy hiểm", "nghiêm trọng"];

export async function verifyBatch(robotId: number, batchId: number): Promise<VerifyResult> {
  const r = await fetch(`/api/verify/${robotId}/${batchId}`, { method: "POST" });
  if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
  return r.json();
}

export function useBackend(): Backend {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [state, setState] = useState<BackendState | null>(null);
  const [series, setSeries] = useState<Record<number, TelemetryMsg[]>>({});
  const [batches, setBatches] = useState<BatchRow[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [wsStatus, setWsStatus] = useState<WsStatus>("connecting");
  const [chainTick, setChainTick] = useState(0);
  const alive = useRef(true);

  const log = (tone: Activity["tone"], text: string, tx?: string) =>
    setActivity((a) => [{ id: ++activityId, ts: Date.now(), tone, text, tx }, ...a].slice(0, 40));

  // Cấu hình + batch: thử lại tới khi backend sẵn sàng
  useEffect(() => {
    alive.current = true;
    const load = async () => {
      while (alive.current) {
        try {
          const [c, b] = await Promise.all([fetch("/api/config"), fetch("/api/batches")]);
          if (c.ok && b.ok) {
            setConfig(await c.json());
            setBatches(await b.json());
            return;
          }
        } catch {
          /* backend chưa chạy – thử lại */
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    };
    load();
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let closed = false;

    const connect = () => {
      setWsStatus("connecting");
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      ws.onopen = () => setWsStatus("open");
      ws.onclose = () => {
        setWsStatus("closed");
        if (!closed) timer = setTimeout(connect, 1500);
      };
      ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handle = (m: any) => {
      switch (m.type) {
        case "snapshot": {
          setState(m.state);
          const s: Record<number, TelemetryMsg[]> = {};
          for (const [rid, hist] of Object.entries(m.history as Record<string, TelemetryMsg[]>)) s[Number(rid)] = hist;
          setSeries(s);
          break;
        }
        case "telemetry":
          setSeries((s) => ({ ...s, [m.robot_id]: [...(s[m.robot_id] ?? []), m].slice(-SERIES_MAX) }));
          setState((st) => st && { ...st, robots: st.robots.map((r) => (r.robot_id === m.robot_id ? { ...r, last: m } : r)) });
          break;
        case "device":
          setState((st) => st && { ...st, robots: st.robots.map((r) => (r.robot_id === m.robot_id ? { ...r, device: m } : r)) });
          break;
        case "batch":
          setBatches((b) => [{ ...m, merkle_root: m.root_hex }, ...b].slice(0, 50));
          log("info", `Neo batch #${m.batch_id} (robot ${m.robot_id}): ${m.n_records} bản ghi, seq ${m.from_seq}–${m.to_seq}`, m.tx_hash);
          break;
        case "incident":
          if (m.status === "PENDING") log("warning", `AI xác nhận ${m.kind} ở robot ${m.robot_id} – đang ghi sự cố lên chuỗi…`);
          else if (m.status === "SENT")
            log("critical", `Sự cố #${m.incident_id} ${m.kind} (${SEVERITY[m.severity]}) đã ghi on-chain`, m.tx_hash);
          else log("critical", `Ghi sự cố thất bại: ${m.error}`);
          break;
        case "command":
          log(m.cmd === "LOCK" ? "critical" : "good", `Gửi lệnh ${m.cmd} (đã ký HMAC) tới robot ${m.robot_id}`);
          break;
        case "chain_event": {
          const rid = m.args.robotId;
          if (m.event === "EmergencyStop" || m.event === "Unlocked") {
            const locked = m.event === "EmergencyStop" ? 1 : 0;
            setState((st) => st && { ...st, robots: st.robots.map((r) => (r.robot_id === rid ? { ...r, chain_state: locked } : r)) });
            if (m.event === "Unlocked") log("good", `Robot ${rid} được SUPERVISOR mở khóa`, m.tx_hash);
          }
          if (m.event === "OperatorCheckedIn" || m.event === "OperatorCheckedOut") {
            const op = m.event === "OperatorCheckedIn" ? m.args.operator : null;
            setState((st) => st && { ...st, robots: st.robots.map((r) => (r.robot_id === rid ? { ...r, current_operator: op } : r)) });
          }
          if (m.event === "OperatorBlocked") log("critical", `Operator ${String(m.args.operator).slice(0, 10)}… bị thu hồi quyền`, m.tx_hash);
          setChainTick((t) => t + 1);
          break;
        }
      }
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { config, state, series, batches, activity, wsStatus, chainTick };
}
