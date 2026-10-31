import { cn } from "@/lib/utils";

export type Severity = "critical" | "high" | "medium" | "low";

const LEVEL: Record<Severity, { bars: number; color: string; label: string }> = {
  critical: { bars: 4, color: "text-sev-high", label: "Critical" },
  high: { bars: 3, color: "text-sev-high", label: "High" },
  medium: { bars: 2, color: "text-sev-medium", label: "Medium" },
  low: { bars: 1, color: "text-sev-low", label: "Low" },
};

/** Exception severity: bar count carries the level, so it reads without colour. */
export function SeverityMark({
  severity,
  showLabel = true,
  className,
}: {
  severity: Severity;
  showLabel?: boolean;
  className?: string;
}) {
  const l = LEVEL[severity];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-caption font-medium", l.color, className)}>
      <span className="inline-flex h-3 items-end gap-[2px]" aria-hidden>
        {[1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className={cn("w-[3px] rounded-[1px]", i <= l.bars ? "bg-current" : "bg-current opacity-20")}
            style={{ height: `${3 + i * 2.25}px` }}
          />
        ))}
      </span>
      {showLabel ? <span className="text-ink">{l.label}</span> : <span className="sr-only">{l.label} severity</span>}
    </span>
  );
}
