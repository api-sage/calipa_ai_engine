"use client";

import { useState } from "react";
import { Amount } from "@/components/calipa/amount";
import { SeverityMark } from "@/components/calipa/severity";
import type { ReportDetail } from "@/lib/api/client";
import { subtractAmounts, sumAmounts } from "@/lib/money";
import { cn } from "@/lib/utils";

type Day = ReportDetail["days"][number];

const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const longFmt = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const dayLabel = (d: string, long = false) => (long ? longFmt : dayFmt).format(new Date(`${d}T00:00:00Z`));
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en")} ${n === 1 ? one : many}`;

/** First, middle and last day under a bar row. */
function Axis({ days }: { days: Day[] }) {
  const ticks = days.length > 2 ? [days[0], days[Math.floor((days.length - 1) / 2)], days[days.length - 1]] : days;
  return (
    <div className="flex justify-between text-caption text-ink-subtle">
      {ticks.map((d) => (
        <span key={d.day} className="figures">
          {dayLabel(d.day)}
        </span>
      ))}
    </div>
  );
}

/**
 * Every day of the approved period as a bar: how many entries Calipa read that day,
 * with the days that raised an exception in the signal colour.
 */
export function ScopeDays({ days }: { days: Day[] }) {
  const [hover, setHover] = useState<Day | null>(null);
  if (days.length === 0) return null;
  const max = Math.max(1, ...days.map((d) => d.entries));
  const entries = days.reduce((a, d) => a + d.entries, 0);
  const flagged = days.filter((d) => d.exceptions > 0).length;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3 text-caption">
        <span className="figures text-ink-muted">
          {plural(days.length, "day")} · {plural(entries, "entry", "entries")}
        </span>
        <span className={cn("figures", flagged ? "text-signal" : "text-ink-subtle")}>
          {flagged ? `Exceptions on ${plural(flagged, "day")}` : "No exceptions"}
        </span>
      </div>
      <div
        role="img"
        aria-label={`${plural(days.length, "day")}, ${plural(entries, "entry", "entries")}, exceptions on ${plural(flagged, "day")}`}
        className="flex h-14 items-end justify-center gap-[2px]"
        onMouseLeave={() => setHover(null)}
      >
        {days.map((d) => (
          <span
            key={d.day}
            onMouseEnter={() => setHover(d)}
            className="flex h-full max-w-3 min-w-0 flex-1 items-end"
          >
            <span
              className={cn(
                "w-full rounded-[2px] transition-colors duration-150",
                d.exceptions ? "bg-signal" : d.entries ? "bg-line-strong" : "bg-surface-3",
                hover && hover.day !== d.day && "opacity-40",
              )}
              style={{ height: d.entries ? `${Math.max(8, (d.entries / max) * 100)}%` : "2px" }}
            />
          </span>
        ))}
      </div>
      {hover ? (
        <div className="flex justify-between text-caption">
          <span className="figures text-ink">{dayLabel(hover.day, true)}</span>
          <span className="figures text-ink-muted">
            {plural(hover.entries, "entry", "entries")}
            {hover.exceptions > 0 && <span className="text-signal"> · {plural(hover.exceptions, "exception")}</span>}
          </span>
        </div>
      ) : (
        <Axis days={days} />
      )}
    </div>
  );
}

function moneyOn(d: Day, currency: string) {
  const m = d.money.find((x) => x.currency === currency);
  return { into: m?.into_transient ?? "0", out: m?.out_of_transient ?? "0" };
}

const isZero = (v: string) => /^-?0*(\.0*)?$/.test(v);

/**
 * Money into the transient account above the line and out of it below, day by day.
 * The account should end every day where it began; a day that does not is marked.
 */
export function TransientFlow({ days }: { days: Day[] }) {
  const currencies = [...new Set(days.flatMap((d) => d.money.map((m) => m.currency)))].sort();
  const [picked, setPicked] = useState<string | null>(null);
  const [hover, setHover] = useState<Day | null>(null);
  // Open on the currency that needs a look: the one with the most days that did not balance.
  const offDays = (c: string) => days.filter((d) => !isZero(subtractAmounts(moneyOn(d, c).into, moneyOn(d, c).out))).length;
  const currency = picked && currencies.includes(picked) ? picked : [...currencies].sort((a, b) => offDays(b) - offDays(a))[0];
  if (!currency) return null;

  const rows = days.map((d) => ({ day: d, ...moneyOn(d, currency) }));
  const into = sumAmounts(rows.map((r) => r.into));
  const out = sumAmounts(rows.map((r) => r.out));
  const left = subtractAmounts(into, out);
  const max = Math.max(1, ...rows.flatMap((r) => [Number(r.into), Number(r.out)]));
  const unbalanced = rows.filter((r) => !isZero(subtractAmounts(r.into, r.out))).length;
  const shown = hover ? rows.find((r) => r.day.day === hover.day) : undefined;

  return (
    <div className="space-y-5 rounded-xl border border-line p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="grid grid-cols-3 gap-x-8 gap-y-1">
          <div className="text-caption text-ink-muted">Into</div>
          <div className="text-caption text-ink-muted">Out of</div>
          <div className="text-caption text-ink-muted">Left in the account</div>
          <Amount value={shown?.into ?? into} currency={currency} size="lg" />
          <Amount value={shown?.out ?? out} currency={currency} size="lg" />
          <Amount
            value={shown ? subtractAmounts(shown.into, shown.out) : left}
            currency={currency}
            size="lg"
            signed
            className={isZero(shown ? subtractAmounts(shown.into, shown.out) : left) ? "text-up" : "text-down"}
          />
        </div>
        {currencies.length > 1 && (
          <div role="radiogroup" aria-label="Currency" className="flex shrink-0 rounded-lg bg-surface-2 p-0.5">
            {currencies.map((c) => (
              <button
                key={c}
                role="radio"
                aria-checked={c === currency}
                onClick={() => setPicked(c)}
                className={cn(
                  "rounded-md px-2.5 py-1 font-mono text-caption outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                  c === currency ? "bg-surface text-ink shadow-raised" : "text-ink-muted hover:text-ink",
                )}
              >
                {c}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <div
          role="img"
          aria-label={`${currency} into and out of the transient account over ${plural(days.length, "day")}; ${plural(unbalanced, "day")} did not balance`}
          className="relative flex h-44 justify-center gap-[3px]"
          onMouseLeave={() => setHover(null)}
        >
          <span className="pointer-events-none absolute inset-x-0 top-1/2 h-px bg-line-strong" />
          {rows.map((r) => {
            const off = !isZero(subtractAmounts(r.into, r.out));
            const dim = hover && hover.day !== r.day.day;
            return (
              <span
                key={r.day.day}
                onMouseEnter={() => setHover(r.day)}
                className={cn("relative flex max-w-5 min-w-0 flex-1 flex-col transition-opacity duration-150", dim && "opacity-35")}
              >
                <span className="flex flex-1 items-end pb-px">
                  <span className="w-full rounded-t-[3px] bg-brand" style={{ height: `${(Number(r.into) / max) * 100}%` }} />
                </span>
                <span className="flex flex-1 items-start pt-px">
                  <span className="w-full rounded-b-[3px] bg-brand/35" style={{ height: `${(Number(r.out) / max) * 100}%` }} />
                </span>
                {off && <span className="absolute -bottom-2.5 left-1/2 size-1.5 -translate-x-1/2 rounded-full bg-down" />}
              </span>
            );
          })}
        </div>
        <div className="pt-2">
          {hover ? (
            <div className="figures text-caption text-ink">{dayLabel(hover.day, true)}</div>
          ) : (
            <Axis days={days} />
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-line pt-3 text-caption text-ink-muted">
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px] bg-brand" /> Into the account
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px] bg-brand/35" /> Out of it
        </span>
        <span className="flex items-center gap-1.5">
          <span className={cn("size-1.5 rounded-full", unbalanced ? "bg-down" : "bg-up")} />
          {unbalanced ? `Did not balance on ${plural(unbalanced, "day")}` : "Balanced every day"}
        </span>
      </div>
    </div>
  );
}

type Finding = ReportDetail["findings"][number];
type Sev = Finding["severity"];

const SEVERITIES: Sev[] = ["critical", "high", "medium", "low"];
const SEV_BG: Record<Sev, string> = { critical: "bg-sev-high", high: "bg-sev-high", medium: "bg-sev-medium", low: "bg-sev-low" };
const TICKS = 48;

/** A row of fixed ticks, as many lit as the share of the total; never none for a count above zero. */
function TickRow({ label, count, total, tone }: { label: React.ReactNode; count: number; total: number; tone: string }) {
  const lit = count ? Math.max(1, Math.round((count / total) * TICKS)) : 0;
  return (
    <li className="grid grid-cols-[minmax(0,9rem)_1fr_4.5rem] items-center gap-4">
      <span className="min-w-0 truncate text-small">{label}</span>
      <span className="flex h-4 gap-[2px]" aria-hidden>
        {Array.from({ length: TICKS }, (_, i) => (
          <span key={i} className={cn("min-w-0 flex-1 rounded-[1px]", i < lit ? tone : "bg-surface-3")} />
        ))}
      </span>
      <span className={cn("figures text-right text-small", count ? "text-ink" : "text-ink-subtle")}>
        {count.toLocaleString("en")}
        <span className="ml-1.5 text-caption text-ink-subtle">{total ? Math.round((count / total) * 100) : 0}%</span>
      </span>
    </li>
  );
}

/** The report's exceptions counted by severity, or by what kind of break they are. */
export function FindingsBreakdown({ findings }: { findings: Finding[] }) {
  const [by, setBy] = useState<"severity" | "type">("severity");
  const exceptions = findings.flatMap((f) => f.exceptions);
  if (exceptions.length === 0) return null;
  const total = exceptions.length;

  const types = new Map<string, { count: number; worst: Sev }>();
  for (const e of exceptions) {
    const sev = e.severity as Sev;
    const t = types.get(e.label);
    if (!t) types.set(e.label, { count: 1, worst: sev });
    else {
      t.count += 1;
      if (SEVERITIES.indexOf(sev) < SEVERITIES.indexOf(t.worst)) t.worst = sev;
    }
  }

  return (
    <div className="space-y-4 rounded-xl border border-line p-5">
      <div className="flex items-center justify-between gap-4">
        <span className="figures text-small text-ink-muted">
          {plural(total, "exception")} in {plural(findings.length, "finding")}
        </span>
        <div role="radiogroup" aria-label="Count by" className="flex shrink-0 rounded-lg bg-surface-2 p-0.5">
          {(["severity", "type"] as const).map((v) => (
            <button
              key={v}
              role="radio"
              aria-checked={by === v}
              onClick={() => setBy(v)}
              className={cn(
                "rounded-md px-2.5 py-1 text-caption outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                by === v ? "bg-surface text-ink shadow-raised" : "text-ink-muted hover:text-ink",
              )}
            >
              {v === "severity" ? "Severity" : "Type"}
            </button>
          ))}
        </div>
      </div>
      <ul className="space-y-2.5" aria-label={`Exceptions by ${by}`}>
        {by === "severity"
          ? SEVERITIES.map((s) => (
              <TickRow
                key={s}
                label={<SeverityMark severity={s} />}
                count={exceptions.filter((e) => e.severity === s).length}
                total={total}
                tone={SEV_BG[s]}
              />
            ))
          : [...types.entries()]
              .sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0]))
              .map(([label, t]) => <TickRow key={label} label={<span title={label}>{label}</span>} count={t.count} total={total} tone={SEV_BG[t.worst]} />)}
      </ul>
    </div>
  );
}
