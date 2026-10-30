"use client";

import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Ban, Check, Hand, Minus, X } from "lucide-react";
import { duration, type Stage, type StageStatus } from "@/lib/progress";
import { cn } from "@/lib/utils";

function Node({ status }: { status: StageStatus }) {
  const base = "relative z-10 grid size-6 place-items-center rounded-full";
  switch (status) {
    case "done":
      return (
        <span className={cn(base, "bg-up text-white dark:text-canvas")}>
          <Check className="size-3.5" strokeWidth={3} />
        </span>
      );
    case "current":
      return (
        <span className={cn(base, "bg-brand-soft text-brand")}>
          <span className="size-2 rounded-full bg-current animate-pulse-dot" />
        </span>
      );
    case "waiting":
      return (
        <span className={cn(base, "bg-attention-soft text-attention")}>
          <Hand className="size-3.5" strokeWidth={2.25} />
        </span>
      );
    case "failed":
      return (
        <span className={cn(base, "bg-down text-white dark:text-canvas")}>
          <X className="size-3.5" strokeWidth={3} />
        </span>
      );
    case "stopped":
      return (
        <span className={cn(base, "bg-surface-3 text-ink-muted")}>
          <Ban className="size-3.5" strokeWidth={2.25} />
        </span>
      );
    case "skipped":
      return (
        <span className={cn(base, "border border-dashed border-line-strong text-ink-subtle")}>
          <Minus className="size-3" />
        </span>
      );
    default:
      return <span className={cn(base, "border border-line-strong bg-surface")} />;
  }
}

/** Re-renders every second while a stage is running, so its clock moves. */
function useTick(active: boolean) {
  const [, setT] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setT((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

export function ProgressRail({ stages }: { stages: Stage[] }) {
  useTick(stages.some((s) => s.status === "current"));
  return (
    <ol className="relative">
      {stages.map((s, i) => {
        const live = s.status === "current" || s.status === "waiting";
        const explain = live || s.status === "stopped" || s.status === "failed";
        const reached = s.status !== "pending" && s.status !== "skipped";
        return (
          <li key={s.key} className="relative flex gap-3 pb-5 last:pb-0">
            {i < stages.length - 1 && (
              <span className="absolute top-6 bottom-0 left-3 w-px -translate-x-1/2 bg-line-strong">
                <motion.span
                  className="absolute inset-x-0 top-0 bg-up"
                  initial={false}
                  animate={{ height: s.status === "done" ? "100%" : "0%" }}
                  transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                />
              </span>
            )}
            <Node status={s.status} />
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className={cn("text-small font-medium", reached ? "text-ink" : "text-ink-subtle")}>{s.label}</span>
                {s.enteredAt && (s.leftAt || live) && (
                  <span className={cn("figures shrink-0 text-caption", live ? "text-brand-ink" : "text-ink-subtle")}>
                    {duration(s.enteredAt, s.leftAt)}
                  </span>
                )}
              </div>
              {explain && <p className="mt-0.5 text-caption text-ink-muted text-pretty">{s.hint}</p>}
              {s.status === "skipped" && <p className="mt-0.5 text-caption text-ink-subtle">Not needed</p>}
              {s.detail.length > 0 && reached && (
                <p className="figures mt-0.5 text-caption text-ink-subtle">{s.detail.join(" · ")}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
