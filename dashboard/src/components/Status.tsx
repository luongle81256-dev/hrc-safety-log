// Nhãn trạng thái: màu + icon + chữ (không bao giờ chỉ dùng màu)
export type Tone = "good" | "warning" | "critical" | "neutral";
const ICON: Record<Tone, string> = { good: "✓", warning: "!", critical: "✕", neutral: "–" };

export function Status({ tone, label, big, testId }: { tone: Tone; label: string; big?: boolean; testId?: string }) {
  return (
    <span className={`status ${tone}${big ? " big" : ""}`} data-testid={testId} data-tone={tone}>
      <span className="ico" aria-hidden="true">{ICON[tone]}</span>
      {label}
    </span>
  );
}

export const ZONE_UI: Record<number, { tone: Tone; label: string }> = {
  0: { tone: "good", label: "SAFE" },
  1: { tone: "warning", label: "WARNING" },
  2: { tone: "critical", label: "DANGER" },
};
