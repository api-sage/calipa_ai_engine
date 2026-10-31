"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, BookOpenCheck, FileSearch, MessageCircleQuestion, Play, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { ConfirmDialog, ErrorDialog } from "@/components/calipa/dialogs";
import { DeniedArt } from "@/components/calipa/illustrations";
import { EmptyState } from "@/components/calipa/states";
import { ACCOUNT_ROLES, objectiveLabel } from "@/lib/labels";
import { useCreateRun, useMe } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

// Requests the interpreter handles well today (from the P1 evals).
const EXAMPLES = [
  "Check the fee and VAT legs for all FCY transfers last month across all accounts",
  "Reconcile the transient account for yesterday",
  "Were all FX conversions last week done at the recorded rate? All accounts.",
  "Look for duplicate transfers in September 2026 on all accounts",
  "Trace transaction FT-20261025-0001 on 25 October 2026",
];

const STEPS = [
  { icon: MessageCircleQuestion, title: "Calipa reads your request", body: "It works out the period, accounts and checks. If anything is unclear, it asks before going further." },
  { icon: FileSearch, title: "It reads and reconciles the ledger", body: "Read-only calls to the bank. Every transfer is checked; anything that does not add up is explained with evidence." },
  { icon: BookOpenCheck, title: "You get a report to review", body: "Every figure comes from the data. A second person approves it before it counts." },
];

const MAX = 4000;

function newKey() {
  return `ui-${crypto.randomUUID()}`;
}

export function NewAudit() {
  const me = useMe();
  const router = useRouter();
  const create = useCreateRun();
  const [text, setText] = useState("");
  // One key per draft: a retry after a timeout returns the same run instead of a second one.
  const [key, setKey] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => setKey(newKey()), []);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
  }, [text]);

  const ready = text.trim().length > 0 && text.length <= MAX && !!key && !create.isPending;

  // Starting an audit spends money (AI calls), so it is confirmed first.
  function submit() {
    if (ready) setConfirming(true);
  }

  function start() {
    if (!ready || !key) return;
    create.mutate(
      { text: text.trim(), key },
      {
        onSuccess: (run) => {
          setConfirming(false);
          setKey(newKey());
          router.push(`/runs/${run.run_id}`);
        },
        onError: (e) => {
          setConfirming(false);
          setError(e);
        },
      },
    );
  }

  if (me.data && !me.data.can_initiate) {
    return (
      <EmptyState
        art={DeniedArt}
        title="You review audits; you don't start them"
        body={`${me.data.user} is entitled to review reports only. Ask an auditor to start one, or switch user in development.`}
        className="rounded-2xl border border-line"
      />
    );
  }

  return (
    <div className="space-y-10">
      <div
        className={cn(
          "rounded-2xl border bg-surface shadow-raised transition-colors",
          create.isError ? "border-down/40" : "border-line focus-within:border-brand/50",
        )}
      >
        <label htmlFor="request" className="sr-only">
          What should Calipa check?
        </label>
        <textarea
          id="request"
          ref={area}
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          rows={3}
          placeholder="What should Calipa check? For example: reconcile the transient account for yesterday"
          className="block w-full resize-none bg-transparent px-5 pt-5 pb-2 font-serif text-[1.25rem] leading-[1.5] outline-none placeholder:text-ink-subtle"
        />
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-1 pb-4">
          <span className="flex items-center gap-1.5 text-caption text-ink-subtle">
            <ShieldCheck className="size-3.5 text-verified" />
            Read-only. Calipa cannot move money or change the ledger.
          </span>
          <div className="flex items-center gap-3">
            <span className={cn("figures text-caption", text.length > MAX ? "text-down" : "text-ink-subtle")}>
              {text.length > MAX - 400 && `${text.length} / ${MAX}`}
            </span>
            <span className="text-caption text-ink-subtle max-sm:hidden">
              <Kbd>⌘</Kbd> <Kbd>Enter</Kbd>
            </span>
            <Button variant="brand" onClick={submit} disabled={!ready} loading={create.isPending}>
              Start audit <ArrowRight />
            </Button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Start this audit?"
        description="Calipa reads the request, then fetches and reconciles the ledger. It calls Claude several times along the way, and every step is recorded in the audit trail."
        confirm="Start audit"
        icon={Play}
        pending={create.isPending}
        onConfirm={start}
      >
        <div className="space-y-3">
          <blockquote className="rounded-lg bg-surface-2 px-4 py-3 font-serif text-lead text-ink text-pretty">“{text.trim()}”</blockquote>
          {me.data && (
            <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5 text-caption">
              <dt className="text-ink-subtle">You may audit</dt>
              <dd className="text-ink">{me.data.account_roles.map((r) => ACCOUNT_ROLES[r] ?? r).join(", ")}</dd>
              <dt className="text-ink-subtle">Objectives</dt>
              <dd className="text-ink">{me.data.objectives.map(objectiveLabel).join(", ")}</dd>
              <dt className="text-ink-subtle">Longest period</dt>
              <dd className="figures text-ink">{me.data.max_period_days} days</dd>
            </dl>
          )}
          <p className="text-caption text-ink-muted">Anything outside this is refused before any data is read.</p>
        </div>
      </ConfirmDialog>
      <ErrorDialog error={error} onOpenChange={(o) => !o && setError(null)} onRetry={start} retryLabel="Try again" />

      <section className="space-y-3">
        <h2 className="text-small font-medium text-ink-muted">Try one of these</h2>
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => {
                setText(ex);
                area.current?.focus();
              }}
              className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-small text-ink-muted outline-none transition-all hover:border-line-strong hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50 active:scale-[0.98]"
            >
              {ex}
            </button>
          ))}
        </div>
      </section>

      <section className="grid gap-6 border-t border-line pt-8 md:grid-cols-3">
        {STEPS.map((s, i) => (
          <div key={s.title} className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="grid size-7 place-items-center rounded-lg bg-surface-2 text-ink-muted">
                <s.icon className="size-4" />
              </span>
              <span className="figures font-mono text-caption text-ink-subtle">0{i + 1}</span>
            </div>
            <div className="text-body font-medium">{s.title}</div>
            <p className="text-small text-ink-muted text-pretty">{s.body}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
