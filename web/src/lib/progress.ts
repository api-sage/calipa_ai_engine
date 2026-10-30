import type { EventView } from "@/lib/api/client";
import { RUN_STATE_META, type RunState } from "@/lib/run-state";

// The run's journey as six stages a person recognises, derived from its events.

export type StageStatus = "done" | "current" | "waiting" | "failed" | "stopped" | "skipped" | "pending";

export type Stage = {
  key: string;
  label: string;
  hint: string;
  status: StageStatus;
  enteredAt: string | null;
  leftAt: string | null;
  detail: string[]; // "2 attempts", "3 findings"
};

const STAGES: { key: string; label: string; hint: string; states: RunState[] }[] = [
  { key: "interpret", label: "Read the request", hint: "P1 turns the request into a scope; V1 checks it", states: ["RECEIVED", "INTERPRETING", "CLARIFICATION_REQUESTED"] },
  { key: "extract", label: "Fetch the ledger", hint: "Read-only calls to the bank for transfers and rates", states: ["EXTRACTING", "EXTRACTION_PARTIAL"] },
  { key: "reconcile", label: "Reconcile", hint: "Rules run on every transfer; failures become exceptions", states: ["RECONCILING", "NO_EXCEPTIONS"] },
  { key: "analyse", label: "Explain exceptions", hint: "P2 explains each group; V2 drops what evidence cannot back", states: ["ANALYSING"] },
  { key: "report", label: "Write the report", hint: "P3 writes; V3 checks every figure and word", states: ["REPORTING"] },
  { key: "review", label: "Second-person review", hint: "Someone other than the author approves or rejects", states: ["PENDING_REVIEW", "APPROVED", "REJECTED", "REINVESTIGATION"] },
];

const ENDED: RunState[] = ["DENIED", "UNSUPPORTED", "ESCALATED", "FAILED"];

function stageOf(state: string): number {
  return STAGES.findIndex((s) => s.states.includes(state as RunState));
}

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

export function deriveStages(events: EventView[], current: RunState | null): Stage[] {
  const transitions = events.filter((e) => e.event_type === "transition" && e.state);
  const entered: (string | null)[] = STAGES.map(() => null);
  const left: (string | null)[] = STAGES.map(() => null);
  let last = -1;
  for (const t of transitions) {
    const i = stageOf(t.state!);
    if (i >= 0) {
      if (entered[i] === null) entered[i] = t.created_at;
      for (let j = 0; j < i; j++) if (entered[j] !== null && left[j] === null) left[j] = t.created_at;
      last = Math.max(last, i);
    } else if (last >= 0 && left[last] === null && ENDED.includes(t.state as RunState)) {
      left[last] = t.created_at;
    }
  }

  const count = (type: string) => events.filter((e) => e.event_type === type).length;
  const detail: Record<string, string[]> = {
    interpret: [count("interpretation_attempt") && plural(count("interpretation_attempt"), "attempt"), count("clarification_answered") && plural(count("clarification_answered"), "clarification")].filter(Boolean) as string[],
    extract: [count("extraction_checkpoint") && plural(count("extraction_checkpoint"), "day", "days") + " fetched"].filter(Boolean) as string[],
    reconcile: transitions.some((t) => t.state === "NO_EXCEPTIONS") ? ["No exceptions found"] : [],
    analyse: [count("finding_recorded") && plural(count("finding_recorded"), "finding"), count("analysis_attempt") && plural(count("analysis_attempt"), "AI attempt")].filter(Boolean) as string[],
    report: [count("report_attempt") && plural(count("report_attempt"), "AI attempt")].filter(Boolean) as string[],
    review: [],
  };

  const now = current ? stageOf(current) : -1;
  const ended = current && ENDED.includes(current);
  const at = ended ? last : now;

  return STAGES.map((s, i) => {
    let status: StageStatus;
    if (i < at) status = entered[i] ? "done" : "skipped";
    else if (i === at) {
      if (ended) status = current === "FAILED" ? "failed" : "stopped";
      else if (current === "CLARIFICATION_REQUESTED" || current === "PENDING_REVIEW" || current === "EXTRACTION_PARTIAL") status = "waiting";
      else if (current === "APPROVED") status = "done";
      else if (current === "REJECTED") status = "failed";
      else status = "current";
    } else status = "pending";
    // While a stage is waiting or stopped, say why rather than what the stage does.
    const hint = status === "waiting" || status === "stopped" || status === "failed" ? RUN_STATE_META[current!].hint : s.hint;
    return { key: s.key, label: s.label, hint, status, enteredAt: entered[i], leftAt: left[i], detail: detail[s.key] };
  });
}

export function duration(from: string, to: string | null): string {
  const ms = (to ? new Date(to) : new Date()).getTime() - new Date(from).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${Math.floor(h / 24)} days`;
}
