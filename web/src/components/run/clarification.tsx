"use client";

import { useState } from "react";
import { motion } from "motion/react";
import { CornerDownLeft } from "lucide-react";
import { toast } from "sonner";
import { ErrorDialog } from "@/components/calipa/dialogs";
import { QuestionArt } from "@/components/calipa/illustrations";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import type { RunView } from "@/lib/api/client";
import { useAnswer } from "@/lib/api/hooks";

/** The run is waiting for the auditor: show Calipa's question and take the answer. */
export function Clarification({ run }: { run: RunView }) {
  const [answer, setAnswer] = useState("");
  const send = useAnswer(run.run_id);
  const [error, setError] = useState<unknown>(null);
  const ready = answer.trim().length > 0 && !send.isPending;

  function submit() {
    if (!ready) return;
    send.mutate(answer.trim(), {
      onSuccess: () => {
        setAnswer("");
        toast.success("Answer sent. Calipa is reading the request again.");
      },
      onError: (e) => setError(e),
    });
  }

  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="overflow-hidden rounded-xl border border-attention/30 bg-attention-soft"
    >
      <ErrorDialog error={error} onOpenChange={(o) => !o && setError(null)} onRetry={submit} />
      <div className="flex gap-4 p-5">
        <QuestionArt className="-my-2 h-20 w-24 shrink-0 text-attention/70" />
        <div className="min-w-0 space-y-1 self-center">
          <div className="text-small font-medium text-attention">Calipa needs an answer to continue</div>
          <p className="font-serif text-title text-ink text-pretty">{run.question || "The request was unclear."}</p>
        </div>
      </div>
      {run.can_answer ? (
        <div className="border-t border-attention/20 bg-surface p-3">
          <label htmlFor="clarification" className="sr-only">
            Your answer
          </label>
          <textarea
            id="clarification"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
            }}
            rows={3}
            maxLength={4000}
            placeholder="For example: all accounts, 1 to 30 September 2026"
            className="w-full resize-none bg-transparent px-2 py-1.5 text-body outline-none placeholder:text-ink-subtle"
          />
          <div className="flex items-center justify-between px-2">
            <span className="text-caption text-ink-subtle">
              <Kbd>⌘</Kbd> <Kbd>Enter</Kbd> to send
            </span>
            <Button size="sm" onClick={submit} disabled={!ready} loading={send.isPending}>
              Send answer <CornerDownLeft />
            </Button>
          </div>
        </div>
      ) : (
        <div className="border-t border-attention/20 bg-surface px-5 py-3 text-small text-ink-muted">
          Waiting for {run.initiated_by} to answer.
        </div>
      )}
    </motion.section>
  );
}
