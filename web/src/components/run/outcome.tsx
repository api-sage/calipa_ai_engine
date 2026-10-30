"use client";

import { useEffect, useState } from "react";
import { Loader } from "lucide-react";
import { BrokenArt, DeniedArt, LostArt, NoMatchArt, SealedArt, StalledArt, type Art } from "@/components/calipa/illustrations";
import { duration } from "@/lib/progress";
import type { EventView, RunView } from "@/lib/api/client";
import { RUN_STATE_META, type RunState } from "@/lib/run-state";
import { cn } from "@/lib/utils";

const COPY: Partial<Record<RunState, { title: string; body: string; art: Art; tone?: "down" | "attention" }>> = {
  DENIED: { title: "This audit is outside what you may check", body: "Calipa's policy check refused the scope. Nothing was read from the bank.", art: DeniedArt },
  UNSUPPORTED: { title: "Calipa cannot audit this yet", body: "The request asks for something Calipa does not support. Nothing was read from the bank.", art: LostArt },
  ESCALATED: { title: "Handed to a person", body: "Calipa stopped and escalated rather than guess.", art: NoMatchArt, tone: "attention" },
  FAILED: { title: "The audit stopped on an error", body: "The error is recorded in the audit trail. Start the audit again once it is resolved.", art: BrokenArt, tone: "down" },
  EXTRACTION_PARTIAL: { title: "Some of the ledger could not be fetched", body: "Calipa will not report on partial data without saying so.", art: BrokenArt, tone: "attention" },
  NO_EXCEPTIONS: { title: "Every check passed", body: "Nothing in the approved scope failed a control, so there is nothing to explain or review.", art: SealedArt },
};

// Same threshold as the API's calipa_runs_stalled metric.
const STALL_MS = 15 * 60 * 1000;
// A worker polls every 2s and records worker_claimed before it does anything, so a
// hand-off from a person that sits this long means no worker is running.
const UNCLAIMED_MS = 30 * 1000;

/** Re-renders every few seconds so time-based states appear without new data. */
function useNow(every = 5000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(t);
  }, [every]);
  return now;
}

function Panel({ art: Illustration, title, body, tone, children }: { art: Art; title: string; body: string; tone?: "down" | "attention"; children?: React.ReactNode }) {
  return (
    <section
      className={cn(
        "flex flex-col items-center gap-4 rounded-2xl border px-8 py-10 text-center",
        tone === "down" ? "border-down/25 bg-down-soft/60" : tone === "attention" ? "border-attention/25 bg-attention-soft/60" : "border-line bg-canvas/50",
      )}
    >
      <Illustration />
      <div className="max-w-md space-y-1.5">
        <h2 className="font-serif text-title font-medium text-balance">{title}</h2>
        <p className="text-small text-ink-muted text-pretty">{body}</p>
      </div>
      {children}
    </section>
  );
}

/** Why a run ended without a report, or what it is doing while there is none yet. */
export function Outcome({ run, events }: { run: RunView; events: EventView[] }) {
  const state = run.state as RunState;
  const meta = RUN_STATE_META[state];
  const copy = COPY[state];
  const now = useNow();
  const last = events.at(-1);
  const lastEventAt = last?.created_at;
  const idle = lastEventAt ? now - new Date(lastEventAt).getTime() : 0;

  if (meta.family === "working") {
    // The API wrote the last event for a person (the run was started, or a question
    // answered) and no worker has claimed the run since.
    const answered = last?.event_type === "clarification_answered" || events.at(-2)?.event_type === "clarification_answered";
    const handedOff = !!last && !last.actor.startsWith("worker:") && (answered || last.actor === run.initiated_by);
    if (handedOff && idle > UNCLAIMED_MS && idle <= STALL_MS) {
      return (
        <Panel
          art={StalledArt}
          tone="attention"
          title="Waiting for a worker to pick this up"
          body={`Calipa has your ${answered ? "answer" : "request"}, but no worker has started on it. Audits only move while a worker is running; this one carries on by itself when one starts, and nothing is lost.`}
        />
      );
    }
    if (lastEventAt && idle > STALL_MS) {
      return (
        <Panel
          art={StalledArt}
          tone="attention"
          title={`No progress for ${duration(lastEventAt, null)}`}
          body={`This audit is at “${meta.label.toLowerCase()}” but nothing has happened since. The worker that runs audits may be stopped.`}
        />
      );
    }
    return (
      <section className="flex items-start gap-3 rounded-xl border border-line p-5">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-brand-soft text-brand">
          <Loader className="size-4 animate-spin [animation-duration:2.4s]" />
        </span>
        <div className="space-y-1">
          <div className="text-body font-medium">{meta.label}</div>
          <p className="text-small text-ink-muted">{meta.hint}. The report appears here when it is written; you can leave this page.</p>
        </div>
      </section>
    );
  }
  if (!copy) return null;
  return (
    <Panel art={copy.art} title={copy.title} body={copy.body} tone={copy.tone}>
      {run.reason && <p className="max-w-lg rounded-lg bg-surface px-4 py-2.5 text-left text-small text-ink text-pretty shadow-raised">{run.reason}</p>}
    </Panel>
  );
}
