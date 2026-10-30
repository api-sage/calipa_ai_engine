"use client";

import { useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, Check, RotateCcw, ShieldCheck, TriangleAlert, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog, ErrorDialog } from "@/components/calipa/dialogs";
import { RefId } from "@/components/calipa/ref-id";
import { Timestamp } from "@/components/calipa/timestamp";
import type { ReviewDecision, RunView } from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import { useReview } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

const DECISIONS: { value: ReviewDecision; label: string; icon: typeof Check; tone: string }[] = [
  { value: "approved", label: "Approve", icon: Check, tone: "data-[on=true]:bg-up-soft data-[on=true]:text-up data-[on=true]:ring-up/40" },
  { value: "reinvestigate", label: "Send back", icon: RotateCcw, tone: "data-[on=true]:bg-attention-soft data-[on=true]:text-attention data-[on=true]:ring-attention/40" },
  { value: "rejected", label: "Reject", icon: X, tone: "data-[on=true]:bg-down-soft data-[on=true]:text-down data-[on=true]:ring-down/40" },
];

const CONFIRM: Record<ReviewDecision, { title: string; body: string; verb: string; tone: "brand" | "attention" | "down" }> = {
  approved: {
    title: "Approve this report?",
    body: "Your approval is signed with your name and the report's hash, goes into the audit trail, and cannot be undone.",
    verb: "Approve report",
    tone: "brand",
  },
  reinvestigate: {
    title: "Send back for another look?",
    body: "Calipa starts a new run of this audit with your reason. It reads the ledger again and uses AI again. This report stays as it is.",
    verb: "Send back",
    tone: "attention",
  },
  rejected: {
    title: "Reject this report?",
    body: "The audit ends here. Your reason goes into the audit trail, and the author can start a new audit.",
    verb: "Reject report",
    tone: "down",
  },
};

const DONE_COPY: Record<ReviewDecision, { title: string; tone: string }> = {
  approved: { title: "Approved", tone: "text-up" },
  rejected: { title: "Rejected", tone: "text-down" },
  reinvestigate: { title: "Sent back for re-investigation", tone: "text-attention" },
};

/** `plain` drops the card frame, for when the panel around it already frames it. */
export function ReviewPanel({ run, plain = false }: { run: RunView; plain?: boolean }) {
  const frame = plain ? "" : "rounded-xl border border-line p-4";
  const [decision, setDecision] = useState<ReviewDecision | null>(null);
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);
  const submit = useReview(run.run_id);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const report = run.report;
  const flags = report?.flags ?? [];

  if (run.review) {
    const r = run.review;
    const copy = DONE_COPY[r.decision];
    return (
      <section className={cn("space-y-3", frame)}>
        <div className="flex items-center gap-2">
          <ShieldCheck className={cn("size-4", copy.tone)} />
          <span className={cn("text-body font-medium", copy.tone)}>{copy.title}</span>
        </div>
        <p className="text-small text-ink-muted">
          by <span className="text-ink">{r.reviewer}</span> · <Timestamp iso={r.created_at} />
        </p>
        {r.note && <blockquote className="border-l-2 border-line-strong pl-3 text-small text-ink text-pretty">{r.note}</blockquote>}
        <div className="flex items-center justify-between text-caption text-ink-subtle">
          <span>Report reviewed</span>
          <RefId value={r.report_sha256} keep={5} />
        </div>
        {r.child_run_id && (
          <Button asChild variant="outline" size="sm" className="w-full">
            <Link href={`/runs/${r.child_run_id}`}>
              Open the re-investigation <ArrowUpRight />
            </Link>
          </Button>
        )}
      </section>
    );
  }

  if (!report) return null;

  if (!run.can_review) {
    const own = run.state === "PENDING_REVIEW";
    return (
      <section className={cn("space-y-1", frame)}>
        <div className="text-body font-medium">{own ? "Waiting for a reviewer" : "Review"}</div>
        <p className="text-small text-ink-muted text-pretty">
          {own
            ? "A second person must approve this report. Its author, and whoever approved its scope, cannot review it."
            : "This report is not open for review."}
        </p>
      </section>
    );
  }

  const needsNote = decision !== null && decision !== "approved";
  const needsAck = decision === "approved" && flags.length > 0;
  const ready = decision !== null && (!needsNote || note.trim().length > 0) && (!needsAck || ack) && !submit.isPending;

  function send() {
    if (!ready || !decision || !report) return;
    submit.mutate(
      { decision, report_sha256: report.report_sha256, note: note.trim(), acknowledge_flags: ack },
      {
        onSuccess: () => {
          setConfirming(false);
          toast.success(DONE_COPY[decision].title, { description: "Your decision is in the audit trail." });
        },
        onError: (e) => {
          setConfirming(false);
          setError(e);
        },
      },
    );
  }

  // A 409 means the report or the run moved on while the reviewer read it: reload, never resubmit.
  const conflict = error instanceof ApiError && error.status === 409;
  const c = decision ? CONFIRM[decision] : null;
  const DecisionIcon = DECISIONS.find((d) => d.value === decision)?.icon;

  return (
    <section className={cn("space-y-4", frame)}>
      <div className="space-y-1">
        <div className="text-body font-medium">Your review</div>
        <p className="text-caption text-ink-muted">
          You are reviewing version {report.version}. If the report changes before you submit, Calipa will ask you to reload.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-1.5" role="radiogroup" aria-label="Decision">
        {DECISIONS.map((d) => (
          <button
            key={d.value}
            role="radio"
            aria-checked={decision === d.value}
            data-on={decision === d.value}
            onClick={() => setDecision(d.value)}
            className={cn(
              "flex flex-col items-center gap-1 rounded-lg bg-surface-2 py-2.5 text-caption font-medium text-ink-muted ring-1 ring-transparent outline-none transition-all hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50 active:scale-[0.97]",
              d.tone,
            )}
          >
            <d.icon className="size-4" strokeWidth={2.25} />
            {d.label}
          </button>
        ))}
      </div>

      <AnimatePresence initial={false}>
        {needsAck && (
          <motion.label
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="flex cursor-pointer gap-2.5 overflow-hidden rounded-lg bg-attention-soft p-3"
          >
            <Checkbox id="ack-flags" checked={ack} onCheckedChange={(v) => setAck(v === true)} className="mt-0.5" />
            <span className="text-small text-ink text-pretty">
              I have read the {flags.length === 1 ? "flag" : `${flags.length} flags`} on this report and approve it regardless.
            </span>
          </motion.label>
        )}
      </AnimatePresence>

      <div className="space-y-1.5">
        <label htmlFor="review-note" className="flex items-baseline justify-between text-caption">
          <span className="font-medium text-ink">{needsNote ? "Reason (required)" : "Note"}</span>
          <span className="figures text-ink-subtle">{note.length} / 4000</span>
        </label>
        <textarea
          id="review-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder={needsNote ? "Say what is wrong or what to look at again" : "Optional"}
          className={cn(
            "w-full resize-none rounded-lg border bg-surface px-3 py-2 text-small outline-none placeholder:text-ink-subtle focus-visible:ring-2 focus-visible:ring-ring/30",
            needsNote && !note.trim() ? "border-attention/50" : "border-line focus-visible:border-brand",
          )}
        />
      </div>

      {decision === "reinvestigate" && (
        <p className="flex gap-2 text-caption text-ink-muted">
          <TriangleAlert className="mt-px size-3.5 shrink-0 text-attention" />
          Sending back starts a new run of this audit with your reason, and uses AI again.
        </p>
      )}

      <Button className="w-full" onClick={() => ready && setConfirming(true)} disabled={!ready}>
        {decision ? `${DECISIONS.find((d) => d.value === decision)!.label}…` : "Choose a decision"}
      </Button>

      {c && report && (
        <ConfirmDialog
          open={confirming}
          onOpenChange={setConfirming}
          title={c.title}
          description={c.body}
          confirm={c.verb}
          tone={c.tone}
          icon={DecisionIcon}
          pending={submit.isPending}
          onConfirm={send}
        >
          <dl className="grid grid-cols-[96px_1fr] gap-x-3 gap-y-2 rounded-lg bg-surface-2 px-4 py-3 text-caption">
            <dt className="text-ink-subtle">Report</dt>
            <dd className="font-mono text-ink">
              v{report.version} · {report.report_sha256.slice(0, 12)}…
            </dd>
            {flags.length > 0 && decision === "approved" && (
              <>
                <dt className="text-ink-subtle">Flags</dt>
                <dd className="text-attention">{flags.length} acknowledged</dd>
              </>
            )}
            {note.trim() && (
              <>
                <dt className="text-ink-subtle">{decision === "approved" ? "Note" : "Reason"}</dt>
                <dd className="line-clamp-4 text-ink text-pretty">{note.trim()}</dd>
              </>
            )}
          </dl>
        </ConfirmDialog>
      )}
      <ErrorDialog
        error={error}
        onOpenChange={(o) => !o && setError(null)}
        onRetry={conflict ? () => location.reload() : send}
        retryLabel={conflict ? "Reload the report" : "Try again"}
      />
    </section>
  );
}
