// Biểu đồ khoảng cách 30 s gần nhất: 1 series (khoảng cách) + 2 ngưỡng tham chiếu S và S+margin.
// Theo dataviz: nét 2px, lưới nhạt, 1 trục y, có crosshair + tooltip khi rê chuột.
import { useEffect, useRef, useState } from "react";

import type { TelemetryMsg } from "../types";
import { ZONE_UI } from "./Status";

const H = 220;
const M = { l: 46, r: 14, t: 10, b: 26 };
const SPAN_MS = 30_000;
const OUT_OF_RANGE = 8190;
// Chữ trên biểu đồ có viền màu nền => luôn đọc được kể cả khi đường dữ liệu chạy qua
const HALO = { fill: "var(--ink-2)", stroke: "var(--surface)", strokeWidth: 4, paintOrder: "stroke" } as const;

export function DistanceChart({ data, yMax }: { data: TelemetryMsg[]; yMax: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, e.contentRect.width)));
    if (box.current) ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const now = data.length ? data[data.length - 1].ts_ms : Date.now();
  const pts = data.filter((d) => d.ts_ms >= now - SPAN_MS);
  const pw = w - M.l - M.r;
  const ph = H - M.t - M.b;
  const x = (ts: number) => M.l + (1 - (now - ts) / SPAN_MS) * pw;
  const y = (mm: number) => M.t + (1 - Math.min(mm, yMax) / yMax) * ph;
  const last = pts[pts.length - 1];
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.ts_ms).toFixed(1)},${y(p.distance_mm).toFixed(1)}`).join("");
  const ticks = [0, 400, 800, 1200].filter((t) => t <= yMax);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!pts.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(x(pts[i].ts_ms) - px) < Math.abs(x(pts[best].ts_ms) - px)) best = i;
    setHover(best);
  };
  const hp = hover !== null ? pts[hover] : null;

  return (
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${w} ${H}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img"
           aria-label="Khoảng cách tới người trong 30 giây gần nhất">
        {last && (
          <>
            {/* vùng nguy hiểm / cảnh báo theo S ở tốc độ hiện tại */}
            <rect x={M.l} y={y(last.s_mm)} width={pw} height={y(0) - y(last.s_mm)}
                  style={{ fill: "color-mix(in srgb, var(--critical) 9%, transparent)" }} />
            <rect x={M.l} y={y(last.warn_mm)} width={pw} height={y(last.s_mm) - y(last.warn_mm)}
                  style={{ fill: "color-mix(in srgb, var(--warning) 11%, transparent)" }} />
          </>
        )}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={w - M.r} y1={y(t)} y2={y(t)} style={{ stroke: "var(--grid)" }} strokeWidth={1} />
            <text x={M.l - 8} y={y(t) + 4} textAnchor="end" fontSize={11} style={{ fill: "var(--muted)" }}>{t}</text>
          </g>
        ))}
        {[30, 20, 10, 0].map((s) => (
          <text key={s} x={M.l + (1 - s / 30) * pw} y={H - 6} textAnchor={s === 0 ? "end" : "middle"} fontSize={11}
                style={{ fill: "var(--muted)" }}>{s === 0 ? "bây giờ" : `−${s} s`}</text>
        ))}
        <text x={4} y={M.t + 4} fontSize={11} style={{ fill: "var(--muted)" }}>mm</text>
        {last && (
          <>
            <line x1={M.l} x2={w - M.r} y1={y(last.s_mm)} y2={y(last.s_mm)} strokeDasharray="5 4" strokeWidth={1.5}
                  style={{ stroke: "var(--critical)" }} />
            <text x={M.l + 6} y={y(last.s_mm) - 5} fontSize={11} style={HALO}>
              S = {last.s_mm} mm (DANGER)
            </text>
            <line x1={M.l} x2={w - M.r} y1={y(last.warn_mm)} y2={y(last.warn_mm)} strokeDasharray="5 4" strokeWidth={1.5}
                  style={{ stroke: "var(--warning)" }} />
            <text x={M.l + 6} y={y(last.warn_mm) - 5} fontSize={11} style={HALO}>
              cảnh báo ≤ {last.warn_mm} mm
            </text>
          </>
        )}
        <line x1={M.l} x2={w - M.r} y1={y(0)} y2={y(0)} style={{ stroke: "var(--axis)" }} />
        <path d={path} fill="none" strokeWidth={2} strokeLinejoin="round" style={{ stroke: "var(--series)" }} />
        {hp && (
          <>
            <line x1={x(hp.ts_ms)} x2={x(hp.ts_ms)} y1={M.t} y2={M.t + ph} style={{ stroke: "var(--axis)" }} />
            <circle cx={x(hp.ts_ms)} cy={y(hp.distance_mm)} r={4.5} strokeWidth={2}
                    style={{ fill: "var(--series)", stroke: "var(--surface)" }} />
          </>
        )}
        {!pts.length && (
          <text x={M.l + pw / 2} y={M.t + ph / 2} textAnchor="middle" fontSize={13} style={{ fill: "var(--muted)" }}>
            Chờ telemetry…
          </text>
        )}
      </svg>
      {hp && (
        <div className="tip" style={{ left: Math.min(x(hp.ts_ms) + 10, w - 170), top: 8 }}>
          <b>{hp.distance_mm >= OUT_OF_RANGE ? "ngoài tầm đo" : `${hp.distance_mm} mm`}</b> · {ZONE_UI[hp.zone].label}
          <br />
          v = {hp.v_robot_mmps} mm/s · relay {hp.estop ? "NGẮT" : "chạy"}
          <br />
          <span style={{ color: "var(--muted)" }}>seq {hp.seq} · {((now - hp.ts_ms) / 1000).toFixed(1)} s trước</span>
        </div>
      )}
    </div>
  );
}
