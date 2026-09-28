import { useState } from "react";

import { verifyBatch } from "../backend";
import type { ChainRecord } from "../chain";
import { addrUrl, short, txUrl } from "../chain";
import type { Activity, AppConfig, BatchRow, VerifyResult } from "../types";
import { Status } from "./Status";

const SEVERITY = ["", "cảnh báo", "nguy hiểm", "nghiêm trọng"];

function TxLink({ cfg, hash }: { cfg: AppConfig; hash: string | null | undefined }) {
  if (!hash) return <span className="hint">—</span>;
  const url = txUrl(cfg, hash);
  return url ? <a className="mono" href={url} target="_blank" rel="noreferrer">{short(hash, 5)} ↗</a>
             : <code title={hash}>{short(hash, 5)}</code>;
}

function Addr({ cfg, a }: { cfg: AppConfig; a?: string }) {
  if (!a || /^0x0+$/.test(a)) return <span className="hint">không quy lỗi người</span>;
  const url = addrUrl(cfg, a);
  return url ? <a className="mono" href={url} target="_blank" rel="noreferrer">{short(a, 4)}</a> : <span className="mono">{short(a, 4)}</span>;
}

const time = (ts?: number) => (ts ? new Date(ts * 1000).toLocaleTimeString("vi-VN") : "—");

/** Lịch sử sự cố – đọc THẲNG từ event on-chain (không qua backend) */
export function IncidentTable({ cfg, rows, error }: { cfg: AppConfig; rows: ChainRecord[]; error: string | null }) {
  return (
    <section className="card">
      <h2>Lịch sử sự cố on-chain</h2>
      <p className="sub">Đọc trực tiếp event <code>EmergencyStop</code> / <code>Unlocked</code> / <code>OperatorBlocked</code> từ contract bằng ethers – không phụ thuộc backend</p>
      {error && <p className="err">Không đọc được chuỗi: {error}</p>}
      <div className="table-wrap">
        <table data-testid="incident-table">
          <thead><tr><th>Giờ</th><th>Sự kiện</th><th>Robot</th><th>Chi tiết</th><th>Bằng chứng</th><th>Giao dịch</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={6} className="empty">Chưa có sự cố nào trên chuỗi</td></tr>}
            {rows.map((r) => (
              <tr key={r.key} data-event={r.event}>
                <td>{time(r.timestamp)}<br /><span className="hint">block {r.block}</span></td>
                <td>{r.event === "EmergencyStop" ? <Status tone="critical" label={`Sự cố #${r.incidentId}`} />
                  : r.event === "Unlocked" ? <Status tone="good" label="Mở khóa" />
                  : <Status tone="warning" label="Khóa operator" />}</td>
                <td>{r.robotId ?? "—"}</td>
                <td>
                  {r.event === "EmergencyStop" && <>Mức {r.severity} ({SEVERITY[r.severity ?? 0]}) · operator <Addr cfg={cfg} a={r.operator} /></>}
                  {r.event === "Unlocked" && <>SUPERVISOR <Addr cfg={cfg} a={r.supervisor} /></>}
                  {r.event === "OperatorBlocked" && <>Operator <Addr cfg={cfg} a={r.operator} /> · {r.violations} vi phạm</>}
                </td>
                <td>{r.evidence ? <code title={r.evidence}>{short(r.evidence, 5)}</code> : "—"}</td>
                <td><TxLink cfg={cfg} hash={r.tx} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Các batch log đã neo + đối soát log off-chain với Merkle root on-chain */
export function BatchTable({ cfg, rows }: { cfg: AppConfig; rows: BatchRow[] }) {
  const [res, setRes] = useState<Record<string, VerifyResult | string>>({});
  const verify = async (b: BatchRow) => {
    const k = `${b.robot_id}/${b.batch_id}`;
    setRes((r) => ({ ...r, [k]: "…" }));
    try {
      const v = await verifyBatch(b.robot_id, b.batch_id);
      setRes((r) => ({ ...r, [k]: v }));
    } catch (e) {
      setRes((r) => ({ ...r, [k]: String((e as Error).message) }));
    }
  };
  return (
    <section className="card">
      <h2>Log đã neo lên chuỗi (Merkle root)</h2>
      <p className="sub">Mỗi batch = 1 giao dịch <code>anchorLog</code>. “Đối soát” tính lại root từ log off-chain và so với root trên chuỗi</p>
      <div className="table-wrap">
        <table data-testid="batch-table">
          <thead><tr><th>Batch</th><th>Dải seq</th><th className="num">Bản ghi</th><th>Merkle root</th><th className="num">Gas</th><th>Giao dịch</th><th>Đối soát</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={7} className="empty">Chưa có batch nào</td></tr>}
            {rows.slice(0, 8).map((b) => {
              const k = `${b.robot_id}/${b.batch_id}`;
              const v = res[k];
              return (
                <tr key={k}>
                  <td>#{b.batch_id}<span className="hint"> · robot {b.robot_id}</span></td>
                  <td className="mono">{b.from_seq}–{b.to_seq}</td>
                  <td className="num">{b.n_records}</td>
                  <td><code title={b.merkle_root}>{short("0x" + b.merkle_root.replace(/^0x/, ""), 5)}</code></td>
                  <td className="num">{b.gas_used?.toLocaleString("vi-VN") ?? "—"}</td>
                  <td><TxLink cfg={cfg} hash={b.tx_hash} /></td>
                  <td data-testid={`verify-${k}`}>
                    {!v && <button className="btn small" onClick={() => verify(b)}>Đối soát</button>}
                    {v === "…" && <span className="hint">đang kiểm…</span>}
                    {typeof v === "string" && v !== "…" && <span className="hint">{v}</span>}
                    {typeof v === "object" && (v.ok
                      ? <Status tone="good" label={`Khớp on-chain (${v.onchain_ok}/${v.onchain_checked})`} />
                      : <Status tone="critical" label={v.hmac_bad_seqs.length ? `BỊ SỬA: seq ${v.hmac_bad_seqs.join(", ")}` : "Root KHÔNG khớp"} />)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ActivityFeed({ cfg, items }: { cfg: AppConfig; items: Activity[] }) {
  return (
    <section className="card">
      <h2>Hoạt động gần đây</h2>
      <p className="sub">Sự cố, lệnh LOCK/UNLOCK, batch – đẩy real-time qua WebSocket</p>
      <ul className="feed" data-testid="activity">
        {items.length === 0 && <li><span /><span /><span className="hint">Chưa có hoạt động</span></li>}
        {items.map((a) => (
          <li key={a.id}>
            <span className={`dot ${a.tone === "info" ? "" : a.tone}`} aria-hidden="true" />
            <time>{new Date(a.ts).toLocaleTimeString("vi-VN")}</time>
            <span>{a.text} {a.tx && <TxLink cfg={cfg} hash={a.tx} />}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
