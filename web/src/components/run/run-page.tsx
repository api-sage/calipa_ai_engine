"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "motion/react";
import { ArrowLeft, CornerLeftUp, Copy, Download, FileText, Link2, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { LostArt } from "@/components/calipa/illustrations";
import { LiveIndicator } from "@/components/calipa/live-indicator";
import { RefId } from "@/components/calipa/ref-id";
import { RunStatePill } from "@/components/calipa/run-state-pill";
import { EmptyState, ErrorState } from "@/components/calipa/states";
import { DateCell, Timestamp } from "@/components/calipa/timestamp";
import { usePageTitle } from "@/components/shell/shell-context";
import { Avatar } from "@/components/shell/user-switch";
import { ApiError, type RunView } from "@/lib/api/client";
import { useEvents, useReport, useRun, useRunStream } from "@/lib/api/hooks";
import { deriveStages } from "@/lib/progress";
import { RUN_STATE_META, type RunState } from "@/lib/run-state";
import { cn } from "@/lib/utils";
import { Activity } from "./activity";
import { Clarification } from "./clarification";
import { Outcome } from "./outcome";
import { ProgressRail } from "./progress-rail";
import { ReportView } from "./report-view";
import { ReviewPanel } from "./review-panel";
import { ScopeCard } from "./scope-card";

type Tab = "review" | "details" | "activity";

/** Whether the side panel fits. Rendered once, in one place, so forms never exist twice. */
function useWide() {
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const on = () => setWide(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <dt className="text-caption text-ink-subtle">{label}</dt>
      <dd className="flex min-h-8 items-center text-small text-ink">{children}</dd>
    </div>
  );
}

function HeaderSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-4 w-20" />
      <Skeleton className="h-9 w-3/4" />
      <div className="flex gap-10">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="space-y-2">
            <Skeleton className="h-3 w-14" />
            <Skeleton className="h-6 w-24" />
          </div>
        ))}
      </div>
    </div>
  );
}

function Actions({ run }: { run: RunView }) {
  const copy = (text: string, what: string) => (navigator.clipboard.writeText(text), toast.success(`${what} copied`));
  return (
    <div className="flex items-center gap-1.5">
      {run.report && (
        <Button asChild variant="outline" size="sm">
          <a href={`/api/runs/${run.run_id}/report.md`} download={`calipa-${run.run_id.slice(0, 8)}-v${run.report.version}.md`}>
            <Download /> Report
          </a>
        </Button>
      )}
      <Button variant="outline" size="sm" onClick={() => copy(location.href, "Link")}>
        <Link2 /> Copy link
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon-sm" aria-label="More actions">
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          {run.report && (
            <DropdownMenuItem asChild>
              <a href={`/api/runs/${run.run_id}/report.md`} target="_blank" rel="noreferrer">
                <FileText /> Open the raw report
              </a>
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => copy(run.run_id, "Audit id")}>
            <Copy /> Copy audit id
          </DropdownMenuItem>
          {run.report && (
            <DropdownMenuItem onSelect={() => copy(run.report!.report_sha256, "Report hash")}>
              <Copy /> Copy report hash
            </DropdownMenuItem>
          )}
          {run.parent_run_id && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild>
                <Link href={`/runs/${run.parent_run_id}`}>
                  <CornerLeftUp /> Open the earlier version
                </Link>
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function Tabs({ tabs, value, onChange }: { tabs: { value: Tab; label: string; dot?: boolean }[]; value: Tab; onChange: (t: Tab) => void }) {
  return (
    <div role="tablist" aria-label="Audit panel" className="flex gap-5 border-b border-line px-5">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            "relative flex h-11 items-center gap-1.5 text-small outline-none transition-colors focus-visible:text-ink",
            value === t.value ? "font-medium text-ink" : "text-ink-muted hover:text-ink",
          )}
        >
          {t.label}
          {t.dot && <span className="size-1.5 rounded-full bg-signal" aria-label="needs you" />}
          {value === t.value && (
            <motion.span layoutId="run-tab" className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-ink" transition={{ type: "spring", duration: 0.3, bounce: 0 }} />
          )}
        </button>
      ))}
    </div>
  );
}

export function RunPage({ id }: { id: string }) {
  const run = useRun(id);
  const state = (run.data?.state ?? null) as RunState | null;
  const family = state ? RUN_STATE_META[state].family : null;
  const events = useEvents(id);
  const stream = useRunStream(id, family === "working");
  const report = useReport(id, !!run.data?.report);
  const stages = useMemo(() => deriveStages(events.data?.events ?? [], state), [events.data, state]);
  const r = run.data;
  const wide = useWide();
  usePageTitle(r ? (r.request_text.length > 56 ? `${r.request_text.slice(0, 56)}…` : r.request_text) : null);

  const reviewable = !!r?.report;
  const needsReview = !!r?.can_review && !r.review && state === "PENDING_REVIEW";
  const [tab, setTab] = useState<Tab>("details");
  // Open on Review when there is something to decide; otherwise on Details.
  useEffect(() => {
    if (r) setTab((t) => (t === "review" && !reviewable ? "details" : needsReview ? "review" : t));
  }, [r, reviewable, needsReview]);

  if (run.isError) {
    const notFound = run.error instanceof ApiError && run.error.status === 404;
    return (
      <div className="mx-auto max-w-6xl px-8 py-16">
        {notFound ? (
          <EmptyState
            art={LostArt}
            title="This audit does not exist, or you cannot see it"
            body="Audits are visible to the person who started them and to reviewers entitled to their accounts."
            action={
              <Button asChild variant="outline" size="sm">
                <Link href="/runs">Back to audits</Link>
              </Button>
            }
          />
        ) : (
          <ErrorState error={run.error} onRetry={() => run.refetch()} />
        )}
      </div>
    );
  }

  const versions = r?.versions ?? [];
  const tabs: { value: Tab; label: string; dot?: boolean }[] = [
    ...(reviewable ? [{ value: "review" as const, label: "Review", dot: needsReview }] : []),
    { value: "details", label: "Details" },
    { value: "activity", label: "Activity" },
  ];

  return (
    <div className="flex min-h-full">
      <div className="min-w-0 flex-1 px-10 py-8">
        <div className="mx-auto max-w-[920px]">
          <div className="mb-6 flex h-8 items-center justify-between gap-4">
            <Link href="/runs" className="inline-flex items-center gap-1.5 rounded text-small text-ink-muted outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50">
              <ArrowLeft className="size-4" /> Audits
            </Link>
            {r && <Actions run={r} />}
          </div>

          {!r ? (
            <HeaderSkeleton />
          ) : (
            <header className="space-y-7">
              <h1 className="font-serif text-display font-medium text-balance">
                <span className="text-ink-subtle">“</span>
                {r.request_text}
                <span className="text-ink-subtle">”</span>
              </h1>

              <div className="flex flex-wrap items-end justify-between gap-6 border-b border-line pb-6">
                <dl className="flex flex-wrap gap-x-10 gap-y-4">
                  <Fact label="State">
                    {state && <RunStatePill state={state} />}
                    {family === "working" && <LiveIndicator status={stream} className="ml-3" />}
                  </Fact>
                  <Fact label="Started by">
                    <span className="flex items-center gap-2">
                      <Avatar id={r.initiated_by} className="size-6 text-[10px]" />
                      {r.initiated_by}
                    </span>
                  </Fact>
                  <Fact label="Started">
                    <Timestamp iso={r.created_at} className="text-ink" />
                  </Fact>
                  <Fact label="Audit id">
                    <RefId value={r.run_id} keep={4} className="-ml-1.5 text-small text-ink" />
                  </Fact>
                  {r.report && (
                    <Fact label="Report">
                      <span className="figures font-mono">v{r.report.version}</span>
                    </Fact>
                  )}
                </dl>
              </div>

              {(r.parent_run_id || versions.length > 1) && (
                <div className="-mt-3 flex flex-wrap items-center gap-1.5 text-caption">
                  {r.parent_run_id && (
                    <Link href={`/runs/${r.parent_run_id}`} className="mr-2 inline-flex items-center gap-1 text-brand-ink hover:underline">
                      <CornerLeftUp className="size-3.5" /> Re-investigation of an earlier version
                    </Link>
                  )}
                  {versions.length > 1 && <span className="text-ink-subtle">Versions</span>}
                  {versions.length > 1 &&
                    versions.map((v) => (
                      <Link
                        key={v.run_id}
                        href={`/runs/${v.run_id}`}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors",
                          v.run_id === r.run_id ? "bg-surface-3 text-ink" : "text-ink-muted hover:bg-surface-2",
                        )}
                      >
                        <span className="font-mono">v{v.version}</span>
                        <span className="text-ink-subtle">{RUN_STATE_META[v.state as RunState]?.label}</span>
                      </Link>
                    ))}
                </div>
              )}
            </header>
          )}

          <div className="mt-8 space-y-8 pb-16">
            {r && state === "CLARIFICATION_REQUESTED" && <Clarification run={r} />}
            {r && !r.report && state !== "CLARIFICATION_REQUESTED" && <Outcome run={r} events={events.data?.events ?? []} />}
            {r?.report && <ReportView report={report.data} runId={id} error={report.isError ? report.error : undefined} onRetry={() => report.refetch()} />}
            {!r && <Skeleton className="h-40 w-full rounded-xl" />}
            {/* Narrow windows have no side panel; the decision and the trail follow the report. */}
            {r && !wide && (
              <div className="space-y-8 border-t border-line pt-8">
                {reviewable && <ReviewPanel run={r} />}
                <Activity events={events.data?.events ?? []} chainOk={events.data?.chain_ok} loading={events.isPending} />
              </div>
            )}
          </div>
        </div>
      </div>

      {wide && (
        <aside className="sticky top-0 flex h-[calc(100dvh-4.5rem)] w-[360px] shrink-0 flex-col border-l border-line xl:w-[380px]">
          <Tabs tabs={tabs} value={tab} onChange={setTab} />
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
            {tab === "review" && r && <ReviewPanel run={r} plain />}
            {tab === "details" && (
              <div className="space-y-8">
                <section className="space-y-3">
                  <h3 className="text-small font-medium">Progress</h3>
                  {events.isPending ? <Skeleton className="h-48 w-full rounded-xl" /> : <ProgressRail stages={stages} />}
                </section>
                {report.data && <ScopeCard scope={report.data.detail.scope} />}
                {r && (
                  <section className="space-y-2">
                    <h3 className="text-small font-medium">Record</h3>
                    <dl className="divide-y divide-line">
                      <div className="grid grid-cols-[96px_1fr] gap-3 py-1.5">
                        <dt className="text-caption text-ink-subtle">Created</dt>
                        <dd>
                          <DateCell iso={r.created_at} />
                        </dd>
                      </div>
                      <div className="grid grid-cols-[96px_1fr] items-center gap-3 py-1.5">
                        <dt className="text-caption text-ink-subtle">Audit id</dt>
                        <dd>
                          <RefId value={r.run_id} keep={8} className="-ml-1.5" />
                        </dd>
                      </div>
                      {r.report && (
                        <div className="grid grid-cols-[96px_1fr] items-center gap-3 py-1.5">
                          <dt className="text-caption text-ink-subtle">Report hash</dt>
                          <dd>
                            <RefId value={r.report.report_sha256} keep={6} className="-ml-1.5" />
                          </dd>
                        </div>
                      )}
                      <div className="grid grid-cols-[96px_1fr] items-center gap-3 py-1.5">
                        <dt className="text-caption text-ink-subtle">Trail</dt>
                        <dd className="figures font-mono text-caption text-ink-muted">{events.data ? `${events.data.events.length} events` : "…"}</dd>
                      </div>
                    </dl>
                  </section>
                )}
              </div>
            )}
            {tab === "activity" && <Activity events={events.data?.events ?? []} chainOk={events.data?.chain_ok} loading={events.isPending} />}
          </div>
        </aside>
      )}
    </div>
  );
}
