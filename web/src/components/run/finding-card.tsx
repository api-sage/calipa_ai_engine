"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, CircleSlash, ListChecks, Repeat } from "lucide-react";
import { Amount } from "@/components/calipa/amount";
import { Provenance } from "@/components/calipa/provenance";
import { RefId } from "@/components/calipa/ref-id";
import { SeverityMark, type Severity } from "@/components/calipa/severity";
import type { ClaimView, FindingDetail, HypothesisView, MoneyView } from "@/lib/api/client";
import { cn } from "@/lib/utils";

function Money({ m, signed }: { m: MoneyView | null; signed?: boolean }) {
  if (!m) return <span className="text-ink-subtle">—</span>;
  return <Amount value={m.amount} currency={m.currency} size="sm" signed={signed} />;
}

function Confidence({ level }: { level: HypothesisView["confidence"] }) {
  const n = { high: 3, medium: 2, low: 1 }[level];
  return (
    <span className="inline-flex items-center gap-1.5 text-caption text-ink-muted" title={`${level} confidence`}>
      <span className="inline-flex gap-0.5" aria-hidden>
        {[1, 2, 3].map((i) => (
          <span key={i} className={cn("h-1.5 w-3 rounded-full", i <= n ? "bg-ink" : "bg-line-strong")} />
        ))}
      </span>
      <span className="capitalize">{level}</span>
    </span>
  );
}

const TITLE_CASE = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const EVIDENCE_LABEL: Record<string, string> = {
  engine_exception: "engine result",
  gl_entry: "ledger entries",
  derived_fact: "derived facts",
  rate_snapshot: "rate snapshot",
  tariff_version: "tariff",
  account_catalog: "account catalog",
  reversal_record: "reversal records",
  switch_log: "switch logs",
  settlement_record: "settlement report",
  transaction_status: "transaction status",
  ops_confirmation: "confirmation from Operations",
};

function Claim({ claim, state }: { claim: ClaimView; state: "support" | "contradict" | "dim" | "plain" }) {
  return (
    <li
      className={cn(
        "grid grid-cols-[36px_1fr] gap-3 rounded-lg px-3 py-2 transition-all duration-300",
        state === "support" && "bg-verified-soft",
        state === "contradict" && "bg-down-soft",
        state === "dim" && "opacity-40",
      )}
    >
      <span className="figures pt-px font-mono text-caption text-ink-subtle">{claim.claim_id}</span>
      <div className="min-w-0 space-y-0.5">
        <p className="text-small text-ink text-pretty">{claim.text}</p>
        <p className="text-caption text-ink-subtle">
          {claim.kind === "observation" ? "Observation, cited to the evidence" : `Inference from ${claim.based_on.join(", ")}`}
        </p>
      </div>
    </li>
  );
}

export function FindingCard({ finding, defaultOpen }: { finding: FindingDetail; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [selected, setSelected] = useState<number | null>(null);
  const [allClaims, setAllClaims] = useState(false);
  const labels = [...new Set(finding.exceptions.map((e) => e.label))];
  const hyp = finding.hypotheses.find((h) => h.rank === selected);
  const claims = allClaims ? finding.claims : finding.claims.slice(0, 5);

  const claimState = (id: string) =>
    !hyp ? "plain" : hyp.supporting_claims.includes(id) ? "support" : hyp.contradicting_claims.includes(id) ? "contradict" : "dim";

  return (
    <article id={`finding-${finding.key}`} className="scroll-mt-6 overflow-hidden rounded-xl border border-line">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-5 py-4 text-left outline-none transition-colors hover:bg-canvas/60 focus-visible:bg-canvas/60"
      >
        <span className="figures grid h-7 min-w-9 place-items-center rounded-md bg-surface-2 px-1.5 font-mono text-caption font-medium">
          {finding.key}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-body font-medium">{TITLE_CASE(labels.join(" · "))}</span>
          <span className="figures block text-caption text-ink-subtle">
            {finding.exceptions.length} exception{finding.exceptions.length === 1 ? "" : "s"}
            {finding.hypotheses.length > 0 && ` · ${finding.hypotheses.length} hypotheses`}
            {finding.status !== "analysed" && ` · ${finding.status.replace(/_/g, " ")}`}
          </span>
        </span>
        <SeverityMark severity={finding.severity as Severity} />
        <ChevronDown className={cn("size-4 text-ink-subtle transition-transform duration-300", open && "rotate-180")} />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="space-y-6 border-t border-line px-5 py-5">
              <div className="space-y-2">
                <p className="max-w-[70ch] text-body text-pretty">{finding.narrative.text}</p>
                <Provenance kind={finding.narrative.source === "ai" ? "ai-checked" : "code"} checker="V3" />
              </div>

              <div className="-mx-5 overflow-x-auto border-y border-line">
                <table className="w-full min-w-[720px] text-small">
                  <thead>
                    <tr className="bg-canvas/50 text-left text-caption text-ink-subtle">
                      <th className="py-2 pr-3 pl-5 font-medium">Exception</th>
                      <th className="px-3 py-2 font-medium">Rule</th>
                      <th className="px-3 py-2 font-medium">Transfer</th>
                      <th className="px-3 py-2 text-right font-medium">Expected</th>
                      <th className="px-3 py-2 text-right font-medium">Actual</th>
                      <th className="py-2 pr-5 pl-3 text-right font-medium">Difference</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {finding.exceptions.map((e) => (
                      <tr key={e.exception_id} className="align-middle">
                        <td className="py-2.5 pr-3 pl-5">
                          <div className="flex items-center gap-2">
                            <SeverityMark severity={e.severity as Severity} showLabel={false} />
                            <span>{TITLE_CASE(e.label)}</span>
                          </div>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-caption whitespace-nowrap text-ink-muted">{e.rule_id}</td>
                        <td className="px-3 py-2.5 whitespace-nowrap">{e.transfer_id ? <RefId value={e.transfer_id} keep={4} className="-ml-1.5" /> : "—"}</td>
                        <td className="px-3 py-2.5 text-right">
                          <Money m={e.expected} />
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          <Money m={e.actual} />
                        </td>
                        <td className="py-2.5 pr-5 pl-3 text-right font-medium">
                          <Money m={e.difference} signed />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {finding.status !== "analysed" && (
                <p className="rounded-lg bg-canvas/60 px-3 py-2 text-small text-ink-muted">
                  No AI explanation for this finding: {finding.reason}. The figures above are complete.
                </p>
              )}

              {finding.hypotheses.length > 0 && (
                <section className="space-y-3">
                  <div className="flex items-baseline justify-between gap-3">
                    <h4 className="text-small font-medium">Likely causes, ranked</h4>
                    <span className="text-caption text-ink-subtle">Select one to see the evidence behind it</span>
                  </div>
                  <ol className="space-y-1.5">
                    {finding.hypotheses.map((h) => {
                      const on = selected === h.rank;
                      return (
                        <li key={h.rank}>
                          <button
                            onClick={() => setSelected(on ? null : h.rank)}
                            aria-pressed={on}
                            className={cn(
                              "grid w-full grid-cols-[24px_1fr_auto] gap-x-3 gap-y-1 rounded-lg border px-3 py-3 text-left outline-none transition-all focus-visible:ring-2 focus-visible:ring-ring/50",
                              on ? "border-verified/40 bg-verified-soft/50" : "border-line hover:border-line-strong",
                            )}
                          >
                            <span className="figures pt-px font-mono text-caption text-ink-subtle">{h.rank}</span>
                            <span className="text-small font-medium">{TITLE_CASE(h.label)}</span>
                            <Confidence level={h.confidence} />
                            <span />
                            <span className="col-span-2 text-small text-ink-muted text-pretty">{h.summary}</span>
                            {h.would_confirm.length > 0 && (
                              <>
                                <span />
                                <span className="col-span-2 flex flex-wrap items-center gap-1 pt-1 text-caption text-ink-subtle">
                                  Would confirm:
                                  {h.would_confirm.map((w) => (
                                    <span key={w} className="rounded bg-surface-2 px-1.5 py-px text-ink-muted">
                                      {EVIDENCE_LABEL[w] ?? w}
                                    </span>
                                  ))}
                                </span>
                              </>
                            )}
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                </section>
              )}

              {finding.claims.length > 0 && (
                <section className="space-y-2">
                  <div className="flex flex-wrap items-baseline justify-between gap-3">
                    <h4 className="text-small font-medium">Evidence the analyst relied on</h4>
                    <Provenance kind="ai-checked" checker="V2" />
                  </div>
                  <ul className="-mx-3 space-y-0.5">
                    {claims.map((c) => (
                      <Claim key={c.claim_id} claim={c} state={claimState(c.claim_id)} />
                    ))}
                  </ul>
                  {finding.claims.length > 5 && (
                    <button onClick={() => setAllClaims((a) => !a)} className="text-caption font-medium text-brand-ink hover:underline">
                      {allClaims ? "Show fewer" : `Show all ${finding.claims.length}`}
                    </button>
                  )}
                </section>
              )}

              {(finding.next_steps.length > 0 || finding.data_limitations.length > 0) && (
                <div className="grid gap-4 md:grid-cols-2">
                  {finding.next_steps.length > 0 && (
                    <section className="space-y-2">
                      <h4 className="flex items-center gap-1.5 text-small font-medium">
                        <ListChecks className="size-4 text-ink-subtle" /> Suggested next steps
                      </h4>
                      <ul className="space-y-1 text-small text-ink-muted">
                        {finding.next_steps.map((s) => (
                          <li key={s} className="flex gap-2">
                            <span className="mt-2 size-1 shrink-0 rounded-full bg-ink-subtle" />
                            {s}
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                  {finding.data_limitations.length > 0 && (
                    <section className="space-y-2">
                      <h4 className="text-small font-medium">What the analyst could not see</h4>
                      <ul className="space-y-1 text-small text-ink-muted">
                        {finding.data_limitations.map((s) => (
                          <li key={s} className="flex gap-2">
                            <span className="mt-2 size-1 shrink-0 rounded-full bg-ink-subtle" />
                            {s}
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                </div>
              )}

              <div className="flex flex-wrap gap-x-5 gap-y-1 text-caption text-ink-subtle">
                {finding.dropped_hypotheses > 0 && (
                  <span className="inline-flex items-center gap-1">
                    <CircleSlash className="size-3.5 text-down" />
                    {finding.dropped_hypotheses} hypothes{finding.dropped_hypotheses === 1 ? "is" : "es"} dropped by V2, kept in the audit trail
                  </span>
                )}
                {finding.reused_from_run && (
                  <span className="inline-flex items-center gap-1">
                    <Repeat className="size-3.5" /> Carried over unchanged from the previous version
                  </span>
                )}
                {finding.accounts.length > 0 && (
                  <span>
                    Accounts:{" "}
                    {finding.accounts.map((a, i) => (
                      <span key={a.label}>
                        {i > 0 && ", "}
                        <span className="text-ink-muted">{a.label.replace(/_/g, " ").toLowerCase()}</span>{" "}
                        <span className="figures font-mono">{a.account}</span>
                      </span>
                    ))}
                  </span>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </article>
  );
}
