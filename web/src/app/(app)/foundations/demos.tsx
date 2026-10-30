"use client";

import { useEffect, useState } from "react";
import NumberFlow from "@number-flow/react";
import { Check, Pause, Play, RotateCcw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, ErrorDialog, ShortcutsDialog } from "@/components/calipa/dialogs";
import { RunStatePill } from "@/components/calipa/run-state-pill";
import { ApiError } from "@/lib/api/client";
import type { RunState } from "@/lib/run-state";

// The path the example run took (782eef78…): seven states in about two minutes.
const HAPPY_PATH: RunState[] = [
  "RECEIVED",
  "INTERPRETING",
  "EXTRACTING",
  "RECONCILING",
  "ANALYSING",
  "REPORTING",
  "PENDING_REVIEW",
  "APPROVED",
];

export function RunStateDemo() {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setI((n) => (n + 1) % HAPPY_PATH.length), 1600);
    return () => clearInterval(t);
  }, [playing]);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="w-44">
        <RunStatePill state={HAPPY_PATH[i]} />
      </div>
      <Button variant="ghost" size="icon-sm" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"}>
        {playing ? <Pause /> : <Play />}
      </Button>
      <span className="figures text-caption text-ink-subtle">
        {i + 1} / {HAPPY_PATH.length}
      </span>
    </div>
  );
}

export function LoadingButtonDemo() {
  const [loading, setLoading] = useState(false);
  return (
    <Button
      loading={loading}
      onClick={() => {
        setLoading(true);
        setTimeout(() => setLoading(false), 1800);
      }}
    >
      Start audit
    </Button>
  );
}

/** Live counters while the engine runs. Counts animate; money never does. */
export function CheckCounterDemo() {
  const [passed, setPassed] = useState(0);
  const [failed, setFailed] = useState(0);

  function run() {
    setPassed(0);
    setFailed(0);
    let step = 0;
    const t = setInterval(() => {
      step += 1;
      setPassed(Math.min(42, step * 6));
      if (step === 3 || step === 5 || step === 7) setFailed((f) => f + 1);
      if (step >= 7) clearInterval(t);
    }, 260);
  }

  useEffect(run, []);

  return (
    <div className="flex flex-wrap items-end gap-8">
      <div>
        <div className="text-caption text-ink-muted">Checks passed</div>
        <NumberFlow value={passed} className="figures font-serif text-display font-medium" />
      </div>
      <div>
        <div className="text-caption text-ink-muted">Checks failed</div>
        <NumberFlow value={failed} className="figures font-serif text-display font-medium text-down" />
      </div>
      <Button variant="outline" size="sm" onClick={run}>
        <RotateCcw /> Replay
      </Button>
    </div>
  );
}

/** Opens each dialog with sample content. Nothing is sent anywhere. */
export function DialogDemo() {
  const [confirm, setConfirm] = useState<null | "approve" | "reject" | "back">(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [keys, setKeys] = useState(false);
  const copy = {
    approve: { title: "Approve this report?", description: "Your approval is signed with your name and the report's hash, and cannot be undone.", confirm: "Approve", tone: "brand" as const },
    reject: { title: "Reject this report?", description: "The audit ends here. Your reason goes into the audit trail.", confirm: "Reject", tone: "down" as const },
    back: { title: "Send back for another look?", description: "Calipa starts a new run of this audit with your reason. It uses AI again.", confirm: "Send back", tone: "attention" as const },
  };
  const c = confirm ? copy[confirm] : null;
  return (
    <div className="flex flex-wrap gap-3">
      <Button variant="outline" onClick={() => setConfirm("approve")}>Confirm: approve</Button>
      <Button variant="outline" onClick={() => setConfirm("back")}>Confirm: send back</Button>
      <Button variant="outline" onClick={() => setConfirm("reject")}>Confirm: reject</Button>
      <Button variant="outline" onClick={() => setError(new ApiError(409, "The report changed after you opened it (now version 2). Reload to review the current version."))}>Error: conflict</Button>
      <Button variant="outline" onClick={() => setError(new ApiError(0, "Calipa could not be reached. Check that the API is running."))}>Error: offline</Button>
      <Button variant="outline" onClick={() => setKeys(true)}>Shortcuts</Button>
      {c && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setConfirm(null)}
          title={c.title}
          description={c.description}
          confirm={c.confirm}
          tone={c.tone}
          icon={confirm === "approve" ? ShieldCheck : confirm === "back" ? RotateCcw : Check}
          pending={pending}
          onConfirm={() => {
            setPending(true);
            setTimeout(() => {
              setPending(false);
              setConfirm(null);
              toast.success("Sample only", { description: "Nothing was sent." });
            }, 900);
          }}
        />
      )}
      <ErrorDialog error={error} onOpenChange={(o) => !o && setError(null)} onRetry={() => toast("Retried (sample)")} />
      <ShortcutsDialog open={keys} onOpenChange={setKeys} />
    </div>
  );
}
