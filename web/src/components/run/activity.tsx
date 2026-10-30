"use client";

import { AnimatePresence, motion } from "motion/react";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import type { EventView } from "@/lib/api/client";
import { RUN_STATE_META, type RunState } from "@/lib/run-state";
import { cn } from "@/lib/utils";

const time = new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Lagos", hour: "2-digit", minute: "2-digit", second: "2-digit" });

type Line = { text: string; tone?: "ai" | "ok" | "bad" | "person" };

function describe(e: EventView): Line {
  const p = e.payload as Record<string, unknown>;
  const n = (k: string) => Number(p[k] ?? 0);
  switch (e.event_type) {
    case "transition": {
      const to = RUN_STATE_META[e.state as RunState]?.label ?? e.state;
      return { text: p.from ? `${to}` : "Audit requested", tone: e.actor.includes("@") ? "person" : undefined };
    }
    case "worker_claimed":
      return { text: "A worker picked it up" };
    case "interpretation_attempt": {
      const errors = Array.isArray(p.errors) ? p.errors.length : 0;
      return errors
        ? { text: `P1 attempt ${n("attempt")}: V1 found ${errors} problem${errors > 1 ? "s" : ""}, retrying`, tone: "bad" }
        : { text: `P1 attempt ${n("attempt")}: passed V1`, tone: "ai" };
    }
    case "clarification_answered":
      return { text: "Answer received", tone: "person" };
    case "extraction_checkpoint":
      return { text: `Fetched ${p.day}: ${n("entries")} entries${p.consistent === false ? ", inconsistent" : ""}` };
    case "analysis_attempt":
      return n("errors")
        ? { text: `P2 attempt ${n("attempt")}: V2 found ${n("errors")} problem${n("errors") > 1 ? "s" : ""}, retrying`, tone: "bad" }
        : { text: `P2 attempt ${n("attempt")}: passed V2 with ${n("passing_hypotheses")} hypotheses`, tone: "ai" };
    case "finding_recorded":
      return { text: `Finding recorded (${String(p.status).replace(/_/g, " ")})`, tone: "ok" };
    case "report_attempt":
      return n("errors")
        ? { text: `P3 attempt ${n("attempt")}: V3 found ${n("errors")} problem${n("errors") > 1 ? "s" : ""}, retrying`, tone: "bad" }
        : { text: `P3 attempt ${n("attempt")}: passed V3`, tone: "ai" };
    case "report_rendered":
      return { text: `Report rendered${n("flags") ? `, ${n("flags")} flag${n("flags") > 1 ? "s" : ""}` : ""}`, tone: "ok" };
    case "step_failed":
      return { text: `Step failed: ${String(p.error_type ?? "error")}`, tone: "bad" };
    case "reinvestigation_of":
      return { text: "Sent back for another look", tone: "person" };
    default:
      return { text: e.event_type.replace(/_/g, " ") };
  }
}

const DOT: Record<NonNullable<Line["tone"]> | "none", string> = {
  ai: "bg-ai",
  ok: "bg-up",
  bad: "bg-down",
  person: "bg-attention",
  none: "bg-line-strong",
};

export function Activity({ events, chainOk, loading }: { events: EventView[]; chainOk: boolean | undefined; loading: boolean }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-small font-medium">Audit trail</h3>
        {chainOk !== undefined &&
          (chainOk ? (
            <span className="inline-flex items-center gap-1 text-caption text-verified" title="Every event's hash was recomputed and the chain links up">
              <ShieldCheck className="size-3.5" /> Chain verified
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-caption font-medium text-down">
              <ShieldAlert className="size-3.5" /> Chain broken
            </span>
          ))}
      </div>
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-4 w-full" />
          ))}
        </div>
      ) : (
        <ol className="space-y-0">
          <AnimatePresence initial={false}>
            {[...events].reverse().map((e) => {
              const line = describe(e);
              return (
                <motion.li
                  key={e.seq}
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                  className="grid grid-cols-[52px_10px_1fr] items-baseline gap-2 py-1"
                >
                  <time className="figures font-mono text-[11px] text-ink-subtle" dateTime={e.created_at}>
                    {time.format(new Date(e.created_at))}
                  </time>
                  <span className={cn("size-1.5 translate-y-[-1px] rounded-full", DOT[line.tone ?? "none"])} />
                  <span className="min-w-0 text-caption text-ink-muted">
                    {line.text}
                    {e.actor.includes("@") && <span className="text-ink-subtle"> · {e.actor}</span>}
                  </span>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ol>
      )}
    </div>
  );
}
