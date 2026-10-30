import { cn } from "@/lib/utils";

/** Connection state of the run's event stream. */
export function LiveIndicator({
  status,
  className,
}: {
  status: "live" | "reconnecting" | "offline";
  className?: string;
}) {
  const style = {
    live: { dot: "text-up animate-pulse-dot", label: "Live" },
    reconnecting: { dot: "text-attention animate-pulse", label: "Reconnecting" },
    offline: { dot: "text-ink-subtle", label: "Offline" },
  }[status];
  return (
    <span className={cn("inline-flex items-center gap-2 text-caption font-medium text-ink-muted", className)} role="status">
      <span className={cn("size-1.5 rounded-full bg-current", style.dot)} aria-hidden />
      {style.label}
    </span>
  );
}
