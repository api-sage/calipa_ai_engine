"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import NumberFlow from "@number-flow/react";
import { motion } from "motion/react";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Copy,
  Download,
  ExternalLink,
  Link2,
  MoreHorizontal,
  Plus,
  Search,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { InboxArt, LedgerArt, NoMatchArt } from "@/components/calipa/illustrations";
import { RefId } from "@/components/calipa/ref-id";
import { RunStatePill } from "@/components/calipa/run-state-pill";
import { EmptyState, ErrorState } from "@/components/calipa/states";
import { DateCell } from "@/components/calipa/timestamp";
import { Avatar } from "@/components/shell/user-switch";
import type { RunListItem } from "@/lib/api/client";
import { useRuns } from "@/lib/api/hooks";
import { RUN_STATE_META, RUN_STATES, type RunFamily, type RunState } from "@/lib/run-state";
import { cn } from "@/lib/utils";

type Filter = "all" | "working" | "needs-you" | "done" | "ended";
type Sort = { key: "created" | "state"; dir: "asc" | "desc" };

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "working", label: "In progress" },
  { value: "needs-you", label: "Needs action" },
  { value: "done", label: "Finished" },
  { value: "ended", label: "Stopped or failed" },
];

const PAGE_SIZES = [25, 50, 100];

function matches(filter: Filter, f: RunFamily) {
  return filter === "all" || (filter === "ended" ? f === "stopped" || f === "failed" : f === filter);
}

function family(run: RunListItem): RunFamily {
  return run.state ? RUN_STATE_META[run.state as RunState].family : "working";
}

const stateRank = (r: RunListItem) => (r.state ? RUN_STATES.indexOf(r.state as RunState) : -1);

function Tile({
  label,
  value,
  hint,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number | undefined;
  hint: string;
  tone: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "group relative flex min-w-0 flex-col gap-2 rounded-xl border p-4 text-left outline-none transition-all focus-visible:ring-2 focus-visible:ring-ring/50",
        active ? "border-line-strong bg-surface shadow-raised" : "border-line bg-canvas/50 hover:border-line-strong hover:bg-surface",
      )}
    >
      <span className="flex items-center gap-2 text-small text-ink-muted">
        <span className={cn("size-1.5 rounded-full", tone)} />
        {label}
        {active && <X className="ml-auto size-3.5 text-ink-subtle" />}
      </span>
      <span className="flex h-10 items-center">
        {value === undefined ? (
          <Skeleton className="h-9 w-12" />
        ) : (
          <NumberFlow value={value} className="figures font-serif text-[2.25rem] leading-none font-medium" />
        )}
      </span>
      <span className="truncate text-caption text-ink-subtle">{hint}</span>
    </button>
  );
}

const MIX: { filter: Exclude<Filter, "all">; label: string; tone: string; count: (c: Record<RunFamily, number>) => number }[] = [
  { filter: "working", label: "in progress", tone: "bg-brand", count: (c) => c.working },
  { filter: "needs-you", label: "need action", tone: "bg-attention", count: (c) => c["needs-you"] },
  { filter: "done", label: "finished", tone: "bg-up", count: (c) => c.done },
  { filter: "ended", label: "stopped or failed", tone: "bg-down", count: (c) => c.stopped + c.failed },
];

/** Every audit as one bar, split by where it stands; a part filters the list like its tile. */
function StateMix({ counts, filter, onPick }: { counts: Record<RunFamily, number>; filter: Filter; onPick: (f: Filter) => void }) {
  const parts = MIX.map((m) => ({ ...m, n: m.count(counts) })).filter((m) => m.n > 0);
  return (
    <div className="flex h-2 gap-[3px]" role="group" aria-label="Audits by state">
      {parts.map((m) => (
        <button
          key={m.filter}
          type="button"
          onClick={() => onPick(m.filter)}
          aria-label={`${m.n} ${m.label}`}
          title={`${m.n} ${m.label}`}
          style={{ flexGrow: m.n }}
          className={cn(
            "min-w-1.5 basis-0 rounded-full outline-none transition-opacity duration-200 focus-visible:ring-2 focus-visible:ring-ring/50",
            m.tone,
            filter !== "all" && filter !== m.filter && "opacity-25",
          )}
        />
      ))}
    </div>
  );
}

const COLS = "grid-cols-[minmax(0,1fr)_116px_168px_128px_36px]";
const COLS_QUEUE = "grid-cols-[minmax(0,1fr)_116px_168px_180px_128px_36px]";

function SortHeader({ label, k, sort, onSort, className }: { label: string; k: Sort["key"]; sort: Sort; onSort: (s: Sort) => void; className?: string }) {
  const on = sort.key === k;
  const Icon = sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      onClick={() => onSort({ key: k, dir: on && sort.dir === "desc" ? "asc" : "desc" })}
      className={cn("-ml-1 inline-flex items-center gap-1 rounded px-1 outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50", on && "text-ink", className)}
      aria-label={`Sort by ${label.toLowerCase()}${on ? `, now ${sort.dir === "asc" ? "ascending" : "descending"}` : ""}`}
    >
      {label}
      <Icon className={cn("size-3 transition-opacity", on ? "opacity-100" : "opacity-0")} />
    </button>
  );
}

function RowSkeleton({ queue }: { queue: boolean }) {
  return (
    <div className={cn("grid items-center gap-4 px-5 py-3.5", queue ? COLS_QUEUE : COLS)}>
      <Skeleton className="h-4 w-3/5" />
      <Skeleton className="h-4 w-16" />
      <Skeleton className="h-6 w-28 rounded-full" />
      {queue && <Skeleton className="h-4 w-28" />}
      <Skeleton className="h-8 w-20" />
      <span />
    </div>
  );
}

function RowMenu({ run }: { run: RunListItem }) {
  const url = () => `${location.origin}/runs/${run.run_id}`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        onClick={(e) => e.stopPropagation()}
        aria-label="Actions"
        className="grid size-8 place-items-center rounded-md text-ink-subtle outline-none hover:bg-surface-3 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50 data-[state=open]:bg-surface-3 data-[state=open]:text-ink"
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52" onClick={(e) => e.stopPropagation()}>
        <DropdownMenuItem asChild>
          <Link href={`/runs/${run.run_id}`}>
            <ChevronRight /> Open
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => window.open(`/runs/${run.run_id}`, "_blank")}>
          <ExternalLink /> Open in a new tab
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => (navigator.clipboard.writeText(url()), toast.success("Link copied"))}>
          <Link2 /> Copy link
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => (navigator.clipboard.writeText(run.run_id), toast.success("Audit id copied"))}>
          <Copy /> Copy audit id
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function csv(rows: RunListItem[]) {
  const esc = (v: string) => `"${v.replaceAll('"', '""')}"`;
  const lines = [
    ["audit_id", "state", "request", "initiated_by", "created_at", "parent_audit_id"].join(","),
    ...rows.map((r) =>
      [r.run_id, r.state ?? "", esc(r.request_text), r.initiated_by, r.created_at, r.parent_run_id ?? ""].join(","),
    ),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `calipa-audits-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function PageButton({ onClick, disabled, label, children }: { onClick: () => void; disabled: boolean; label: string; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="grid size-8 place-items-center rounded-md text-ink-muted outline-none transition-colors hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-35"
    >
      {children}
    </button>
  );
}

export function RunsBoard({ view }: { view: "mine" | "review_queue" }) {
  const runs = useRuns(view);
  const router = useRouter();
  const queue = view === "review_queue";
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>({ key: "created", dir: queue ? "asc" : "desc" });
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);

  const counts = useMemo(() => {
    const c: Record<RunFamily, number> = { working: 0, "needs-you": 0, done: 0, stopped: 0, failed: 0 };
    for (const r of runs.data ?? []) c[family(r)] += 1;
    return c;
  }, [runs.data]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = (runs.data ?? []).filter(
      (r) =>
        matches(filter, family(r)) &&
        (!q || r.request_text.toLowerCase().includes(q) || r.run_id.startsWith(q) || r.initiated_by.includes(q)),
    );
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) =>
      sort.key === "created" ? dir * a.created_at.localeCompare(b.created_at) : dir * (stateRank(a) - stateRank(b)) || b.created_at.localeCompare(a.created_at),
    );
  }, [runs.data, filter, query, sort]);

  const pages = Math.max(1, Math.ceil(visible.length / size));
  useEffect(() => setPage(0), [filter, query, sort, size]);
  useEffect(() => setPage((p) => Math.min(p, pages - 1)), [pages]);
  const shown = visible.slice(page * size, page * size + size);

  const total = runs.data?.length;
  const toggle = (f: Filter) => setFilter((cur) => (cur === f ? "all" : f));
  const cols = queue ? COLS_QUEUE : COLS;
  const open = (id: string, e?: React.MouseEvent) => (e?.metaKey || e?.ctrlKey ? window.open(`/runs/${id}`, "_blank") : router.push(`/runs/${id}`));

  return (
    <div className="space-y-6">
      {!queue && runs.data && runs.data.length > 0 && <StateMix counts={counts} filter={filter} onPick={toggle} />}

      {!queue && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Tile label="In progress" value={runs.data && counts.working} hint="Calipa is working on these" tone="bg-brand" active={filter === "working"} onClick={() => toggle("working")} />
          <Tile label="Needs action" value={runs.data && counts["needs-you"]} hint="Waiting on an answer or a review" tone="bg-attention" active={filter === "needs-you"} onClick={() => toggle("needs-you")} />
          <Tile label="Finished" value={runs.data && counts.done} hint="Approved, or no exceptions" tone="bg-up" active={filter === "done"} onClick={() => toggle("done")} />
          <Tile label="Stopped or failed" value={runs.data && counts.stopped + counts.failed} hint="Ended without a signed-off report" tone="bg-down" active={filter === "ended"} onClick={() => toggle("ended")} />
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-line bg-surface">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-1" role="tablist" aria-label="Filter audits">
            {(queue ? FILTERS.slice(0, 1) : FILTERS).map((f) => (
              <button
                key={f.value}
                role="tab"
                aria-selected={filter === f.value}
                onClick={() => setFilter(f.value)}
                className={cn(
                  "relative h-7 rounded-md px-2.5 text-small outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50",
                  filter === f.value ? "text-ink" : "text-ink-muted hover:text-ink",
                )}
              >
                {filter === f.value && (
                  <motion.span layoutId={`filter-${view}`} className="absolute inset-0 rounded-md bg-surface-3" transition={{ type: "spring", duration: 0.3, bounce: 0 }} />
                )}
                <span className="relative">
                  {f.label}
                  {f.value === "all" && total !== undefined && <span className="figures ml-1.5 font-mono text-[11px] text-ink-subtle">{total}</span>}
                </span>
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <label className="relative flex items-center">
              <Search className="pointer-events-none absolute left-2.5 size-4 text-ink-subtle" />
              <span className="sr-only">Filter this list</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && setQuery("")}
                placeholder="Filter by request, id, person"
                className="h-8 w-60 rounded-lg border border-line bg-canvas/60 pr-7 pl-8 text-small outline-none placeholder:text-ink-subtle focus-visible:border-brand focus-visible:bg-surface focus-visible:ring-2 focus-visible:ring-ring/25"
              />
              {query && (
                <button onClick={() => setQuery("")} aria-label="Clear filter" className="absolute right-1.5 grid size-5 place-items-center rounded text-ink-subtle hover:text-ink">
                  <X className="size-3.5" />
                </button>
              )}
            </label>
            <Button variant="outline" size="sm" onClick={() => csv(visible)} disabled={!visible.length} title="Download the rows matching the filter">
              <Download /> CSV
            </Button>
          </div>
        </div>

        <div className={cn("hidden items-center gap-4 border-b border-line bg-canvas/40 px-5 py-2 text-caption font-medium text-ink-subtle md:grid", cols)}>
          <span>Request</span>
          <span>Audit id</span>
          <SortHeader label="State" k="state" sort={sort} onSort={setSort} />
          {queue && <span>Started by</span>}
          <SortHeader label={queue ? "Waiting since" : "Started"} k="created" sort={sort} onSort={setSort} />
          <span />
        </div>

        {runs.isPending ? (
          <div className="divide-y divide-line">
            {Array.from({ length: 8 }, (_, i) => (
              <RowSkeleton key={i} queue={queue} />
            ))}
          </div>
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
        ) : runs.data.length === 0 ? (
          queue ? (
            <EmptyState art={InboxArt} title="Nothing to review" body="Reports waiting for a second person appear here, oldest first. You never review an audit you started." />
          ) : (
            <EmptyState
              art={LedgerArt}
              title="No audits yet"
              body="Describe what to check in plain words. Calipa reads the ledger, reconciles it and writes a report for review."
              action={
                <Button asChild variant="brand" size="sm">
                  <Link href="/runs/new">
                    <Plus /> New audit
                  </Link>
                </Button>
              }
            />
          )
        ) : visible.length === 0 ? (
          <EmptyState
            art={NoMatchArt}
            title="No audits match"
            body={query ? `Nothing has “${query}” in its request, id or author.` : "No audit is in this state right now."}
            action={
              <Button variant="outline" size="sm" onClick={() => (setQuery(""), setFilter("all"))}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <ul className="divide-y divide-line">
            {shown.map((run) => (
              <li key={run.run_id}>
                <div
                  role="link"
                  tabIndex={0}
                  onClick={(e) => open(run.run_id, e)}
                  onKeyDown={(e) => e.key === "Enter" && open(run.run_id)}
                  className={cn(
                    "group grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-3 outline-none transition-colors hover:bg-canvas/60 focus-visible:bg-canvas/60 md:grid",
                    cols,
                  )}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-body text-ink">{run.request_text}</span>
                    {run.parent_run_id && <span className="shrink-0 rounded bg-surface-3 px-1.5 py-px text-[11px] text-ink-muted">Re-run</span>}
                  </div>
                  <span onClick={(e) => e.stopPropagation()} className="max-md:hidden">
                    <RefId value={run.run_id} keep={4} className="-ml-1.5" />
                  </span>
                  <div>{run.state && <RunStatePill state={run.state as RunState} />}</div>
                  {queue && (
                    <span className="flex min-w-0 items-center gap-2 text-small text-ink-muted max-md:hidden">
                      <Avatar id={run.initiated_by} className="size-6 text-[10px]" />
                      <span className="truncate">{run.initiated_by}</span>
                    </span>
                  )}
                  <DateCell iso={run.created_at} className="max-md:hidden" />
                  <span className="max-md:hidden" onClick={(e) => e.stopPropagation()}>
                    <RowMenu run={run} />
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}

        {runs.data && visible.length > 0 && (
          <div className="flex items-center justify-between gap-4 border-t border-line px-3 py-2 text-small text-ink-muted">
            <div className="flex items-center gap-0.5">
              <PageButton onClick={() => setPage(0)} disabled={page === 0} label="First page"><ChevronsLeft className="size-4" /></PageButton>
              <PageButton onClick={() => setPage((p) => p - 1)} disabled={page === 0} label="Previous page"><ChevronLeft className="size-4" /></PageButton>
              <span className="figures min-w-14 text-center font-mono text-caption">
                {page + 1} / {pages}
              </span>
              <PageButton onClick={() => setPage((p) => p + 1)} disabled={page >= pages - 1} label="Next page"><ChevronRight className="size-4" /></PageButton>
              <PageButton onClick={() => setPage(pages - 1)} disabled={page >= pages - 1} label="Last page"><ChevronsRight className="size-4" /></PageButton>
            </div>
            <span className="figures font-mono text-caption text-ink-subtle max-sm:hidden">
              {page * size + 1}–{Math.min(visible.length, page * size + size)} of {visible.length}
            </span>
            <DropdownMenu>
              <DropdownMenuTrigger className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-caption outline-none hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50">
                <span className="figures font-mono">{size}</span> rows <ChevronDown className="size-3.5" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-32">
                <DropdownMenuRadioGroup value={String(size)} onValueChange={(v) => setSize(Number(v))}>
                  {PAGE_SIZES.map((n) => (
                    <DropdownMenuRadioItem key={n} value={String(n)} className="font-mono text-caption">
                      {n} rows
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </div>
    </div>
  );
}
