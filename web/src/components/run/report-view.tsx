"use client";

import { useState } from "react";
import { ChevronDown, Download, TriangleAlert } from "lucide-react";
import NumberFlow from "@number-flow/react";
import { AnimatePresence, motion } from "motion/react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Amount } from "@/components/calipa/amount";
import { Provenance } from "@/components/calipa/provenance";
import { ErrorState } from "@/components/calipa/states";
import type { ReportView as Report } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { FindingCard } from "./finding-card";

function Figure({ label, value, tone, note }: { label: string; value: number; tone?: string; note?: string }) {
  return (
    <div className="min-w-0 space-y-1 px-5 py-4">
      <div className="text-caption text-ink-muted">{label}</div>
      <NumberFlow value={value} className={cn("figures font-serif text-[2.25rem] leading-none font-medium", tone)} />
      {note && <div className="truncate text-caption text-ink-subtle">{note}</div>}
    </div>
  );
}

function Section({ title, aside, children }: { title: string; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-serif text-heading font-medium">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Disclosure({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-line">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-5 py-3.5 text-left text-body font-medium outline-none hover:bg-canvas/60 focus-visible:bg-canvas/60"
      >
        <span>
          {title}
          {count !== undefined && <span className="figures ml-2 font-normal text-ink-subtle">{count}</span>}
        </span>
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
            <div className="border-t border-line">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ReportSkeleton() {
  return (
    <div className="space-y-8">
      <Skeleton className="h-24 w-full rounded-xl" />
      <div className="space-y-2">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-4/5" />
      </div>
      <Skeleton className="h-16 w-full rounded-xl" />
      <Skeleton className="h-16 w-full rounded-xl" />
    </div>
  );
}

export function ReportView({ report, runId, error, onRetry }: { report?: Report; runId: string; error?: unknown; onRetry?: () => void }) {
  if (error) return <ErrorState error={error} onRetry={onRetry} className="rounded-xl border border-line" />;
  if (!report) return <ReportSkeleton />;
  const d = report.detail;
  const r = d.reconciliation;
  const checks = r.rules.reduce((a, x) => ({ passed: a.passed + x.passed, failed: a.failed + x.failed }), { passed: 0, failed: 0 });
  const source = report.summary.narrative_source;

  return (
    <div className="space-y-10">
      {report.summary.flags.length > 0 && (
        <div className="flex gap-3 rounded-xl border border-attention/30 bg-attention-soft p-4" role="note">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-attention" />
          <div className="space-y-1">
            <div className="text-small font-medium text-attention">Flagged for the reviewer</div>
            <ul className="space-y-0.5 text-small text-ink">
              {report.summary.flags.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 divide-line overflow-hidden rounded-xl border border-line md:grid-cols-4 md:divide-x">
        <Figure label="Transfers checked" value={r.transfers_checked} note={r.engine} />
        <Figure label="Checks passed" value={checks.passed} note={`across ${r.rules.length} rules`} />
        <Figure label="Checks failed" value={checks.failed} tone={checks.failed ? "text-down" : undefined} />
        <Figure
          label="Exceptions in scope"
          value={r.exceptions_in_scope}
          note={r.exceptions_outside_scope ? `${r.exceptions_outside_scope} outside the approved controls` : `in ${d.findings.length} finding${d.findings.length === 1 ? "" : "s"}`}
        />
      </div>

      <Section
        title="Summary"
        aside={
          source === "template" ? (
            <span className="text-caption text-ink-subtle">Written by code: no AI narrative</span>
          ) : source === "partial" ? (
            <span className="text-caption text-attention">Part AI, part code: some sections failed V3</span>
          ) : null
        }
      >
        <p className="max-w-[68ch] font-serif text-[1.1875rem] leading-[1.6] text-pretty">{report.executive_summary.text}</p>
        <Provenance kind={report.executive_summary.source === "ai" ? "ai-checked" : "code"} checker="V3" />
      </Section>

      <Section title="Findings" aside={<span className="figures text-caption text-ink-subtle">Most severe first</span>}>
        {d.findings.length === 0 ? (
          <p className="rounded-xl border border-line px-5 py-6 text-small text-ink-muted">No exceptions were found within the approved scope.</p>
        ) : (
          <div className="space-y-3">
            {d.findings.map((f, i) => (
              <FindingCard key={f.key} finding={f} defaultOpen={i === 0} />
            ))}
          </div>
        )}
      </Section>

      {report.patterns.length > 0 && (
        <Section title="Patterns across findings">
          <ul className="space-y-3">
            {report.patterns.map((p) => (
              <li key={p.text} className="flex gap-3">
                <span className="flex shrink-0 gap-1">
                  {p.finding_keys.map((k) => (
                    <a key={k} href={`#finding-${k}`} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-caption hover:bg-surface-3">
                      {k}
                    </a>
                  ))}
                </span>
                <p className="text-body text-pretty">{p.text}</p>
              </li>
            ))}
          </ul>
          <Provenance kind="ai-checked" checker="V3" />
        </Section>
      )}

      <div className="space-y-3">
        <Disclosure title="Reconciliation rules" count={r.rules.length}>
          <table className="w-full text-small">
            <thead>
              <tr className="text-left text-caption text-ink-subtle">
                <th className="py-2 pr-3 pl-5 font-medium">Rule</th>
                <th className="w-1/2 px-3 py-2 font-medium">Result</th>
                <th className="px-3 py-2 text-right font-medium">Passed</th>
                <th className="py-2 pr-5 pl-3 text-right font-medium">Failed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {r.rules.map((x) => {
                const total = x.passed + x.failed || 1;
                return (
                  <tr key={x.rule_id}>
                    <td className="py-2.5 pr-3 pl-5 font-mono text-caption">{x.rule_id}</td>
                    <td className="px-3 py-2.5">
                      <span className="flex h-1.5 overflow-hidden rounded-full bg-surface-2">
                        <span className="bg-up" style={{ width: `${(x.passed / total) * 100}%` }} />
                        <span className="bg-down" style={{ width: `${(x.failed / total) * 100}%` }} />
                      </span>
                    </td>
                    <td className="figures px-3 py-2.5 text-right">{x.passed}</td>
                    <td className={cn("figures py-2.5 pr-5 pl-3 text-right", x.failed ? "font-medium text-down" : "text-ink-subtle")}>{x.failed}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Disclosure>

        {r.control_totals.length > 0 && (
          <Disclosure title="Movements through the transient account" count={r.control_totals.length}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-small">
                <thead>
                  <tr className="text-left text-caption text-ink-subtle">
                    <th className="py-2 pr-3 pl-5 font-medium">Account</th>
                    <th className="px-3 py-2 text-right font-medium">Into transient</th>
                    <th className="px-3 py-2 text-right font-medium">Out of transient</th>
                    <th className="py-2 pr-5 pl-3 text-right font-medium">Entries</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {r.control_totals.map((t) => (
                    <tr key={`${t.account_role}-${t.currency}`}>
                      <td className="py-2.5 pr-3 pl-5">{t.account_role.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}</td>
                      <td className="px-3 py-2.5 text-right">
                        <Amount value={t.credits_to_transient} currency={t.currency} size="sm" />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <Amount value={t.debits_from_transient} currency={t.currency} size="sm" />
                      </td>
                      <td className="figures py-2.5 pr-5 pl-3 text-right text-ink-muted">{t.entries}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Disclosure>
        )}

        <Disclosure title="Limitations" count={d.limitations.length}>
          <ul className="space-y-2 px-5 py-4 text-small text-ink-muted">
            {d.limitations.map((l) => (
              <li key={l} className="flex gap-2 text-pretty">
                <span className="mt-2 size-1 shrink-0 rounded-full bg-ink-subtle" />
                {l}
              </li>
            ))}
          </ul>
        </Disclosure>

        <Disclosure title="How AI was used" count={d.ai_calls.length}>
          <div className="space-y-3 px-5 py-4">
            <p className="max-w-[70ch] text-small text-ink-muted text-pretty">
              AI wrote only the text marked as written by Claude. It never saw account numbers and never wrote a value: every number, date,
              amount and id was inserted by code from the evidence. Every answer was checked by code before use.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-small">
                <thead>
                  <tr className="text-left text-caption text-ink-subtle">
                    <th className="py-2 pr-3 font-medium">Step</th>
                    <th className="px-3 py-2 font-medium">Prompt</th>
                    <th className="px-3 py-2 font-medium">Model</th>
                    <th className="py-2 pl-3 font-medium">Outcome</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {d.ai_calls.map((c, i) => (
                    <tr key={i}>
                      <td className="py-2 pr-3 capitalize">{c.step}</td>
                      <td className="px-3 py-2 font-mono text-caption text-ink-muted">{c.prompt}</td>
                      <td className="px-3 py-2 font-mono text-caption text-ink-muted">{c.model_served ?? c.model_requested}</td>
                      <td className="py-2 pl-3">
                        <span className={cn("text-caption", c.outcome === "ok" ? "text-up" : "text-ink-muted")}>
                          {c.outcome ?? "none"}
                          {c.reused && " · carried over"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Disclosure>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-6">
        <p className="text-caption text-ink-subtle">
          Calipa does not express an audit opinion. That belongs to the reviewing auditor.
        </p>
        <Button asChild variant="outline" size="sm">
          <a href={`/api/runs/${runId}/report.md`} download>
            <Download /> Download report (.md)
          </a>
        </Button>
      </div>
    </div>
  );
}
