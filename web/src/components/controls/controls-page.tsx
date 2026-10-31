"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Lock, Minus, Search, X } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Amount } from "@/components/calipa/amount";
import { NoMatchArt, ScalesArt } from "@/components/calipa/illustrations";
import { RefId } from "@/components/calipa/ref-id";
import { EmptyState, ErrorState, PageHeader } from "@/components/calipa/states";
import type { Me, ReferenceView } from "@/lib/api/client";
import { useMe, useReference } from "@/lib/api/hooks";
import { ACCOUNT_ROLES, EXCEPTION_TYPES, OBJECTIVES, objectiveLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

function Section({ title, description, children, aside }: { title: string; description?: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-serif text-title font-medium">{title}</h2>
          {description && <p className="max-w-2xl text-small text-ink-muted text-pretty">{description}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Yes({ on, children }: { on: boolean; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2.5 text-small">
      <span className={cn("grid size-5 place-items-center rounded-full", on ? "bg-up-soft text-up" : "bg-surface-3 text-ink-subtle")}>
        {on ? <Check className="size-3" strokeWidth={3} /> : <Minus className="size-3" strokeWidth={3} />}
      </span>
      <span className={on ? "text-ink" : "text-ink-muted"}>{children}</span>
    </li>
  );
}

function Access({ me }: { me: Me }) {
  return (
    <div className="grid gap-px overflow-hidden rounded-xl border border-line bg-line md:grid-cols-3">
      <div className="space-y-3 bg-surface p-5">
        <div className="text-caption font-medium text-ink-subtle">What you can do</div>
        <ul className="space-y-2">
          <Yes on={me.can_initiate}>Start audits</Yes>
          <Yes on={me.can_review}>Review other people’s reports</Yes>
        </ul>
      </div>
      <div className="space-y-3 bg-surface p-5">
        <div className="text-caption font-medium text-ink-subtle">Accounts you may audit</div>
        {me.account_roles.length ? (
          <div className="flex flex-wrap gap-1.5">
            {me.account_roles.map((r) => (
              <span key={r} className="rounded-md bg-brand-soft px-2 py-1 text-small text-brand-ink">
                {ACCOUNT_ROLES[r] ?? r}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-small text-ink-muted">None</p>
        )}
      </div>
      <div className="space-y-3 bg-surface p-5">
        <div className="text-caption font-medium text-ink-subtle">Longest period per audit</div>
        {me.max_period_days > 0 ? (
          <div className="flex items-baseline gap-1.5">
            <span className="figures font-serif text-[2.25rem] leading-none font-medium">{me.max_period_days}</span>
            <span className="text-small text-ink-muted">days</span>
          </div>
        ) : (
          <p className="text-small text-ink-muted">You do not start audits</p>
        )}
      </div>
    </div>
  );
}

function Objectives({ ref_, me }: { ref_: ReferenceView; me: Me | undefined }) {
  const names = Object.fromEntries(ref_.controls.map((c) => [c.control_id, c.name]));
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Object.entries(ref_.objectives).map(([o, ids]) => {
        const allowed = !me || me.objectives.includes(o);
        return (
          <article key={o} className={cn("flex flex-col gap-3 rounded-xl border border-line p-4", allowed ? "bg-surface" : "bg-canvas/50")}>
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-1">
                <h3 className="text-body font-medium">{objectiveLabel(o)}</h3>
                <p className="text-small text-ink-muted text-pretty">{OBJECTIVES[o]?.what}</p>
              </div>
              {!allowed && (
                <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-caption text-ink-muted" title="Not in your entitlements">
                  <Lock className="size-3" /> Not yours
                </span>
              )}
            </div>
            <ul className="mt-auto space-y-1 border-t border-line pt-3">
              {ids.map((id) => (
                <li key={id} className="flex items-baseline gap-2 text-caption">
                  <a href={`#${id}`} className="shrink-0 font-mono text-brand-ink hover:underline">
                    {id}
                  </a>
                  <span className="truncate text-ink-muted">{names[id]}</span>
                </li>
              ))}
            </ul>
          </article>
        );
      })}
    </div>
  );
}

function useHashFlash(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    function flash() {
      const id = decodeURIComponent(location.hash.slice(1));
      const el = id && document.getElementById(id);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.dataset.flash = "true";
      setTimeout(() => delete el.dataset.flash, 1600);
    }
    flash();
    window.addEventListener("hashchange", flash);
    return () => window.removeEventListener("hashchange", flash);
  }, [ready]);
}

export function ControlsPage() {
  const reference = useReference();
  const me = useMe();
  const [q, setQ] = useState("");
  useHashFlash(!!reference.data);

  const controls = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (reference.data?.controls ?? []).filter(
      (c) =>
        !s ||
        [c.control_id, c.name, ...c.exception_types, ...c.exception_types.map((t) => EXCEPTION_TYPES[t] ?? ""), ...c.objectives.map(objectiveLabel)]
          .join(" ")
          .toLowerCase()
          .includes(s),
    );
  }, [reference.data, q]);

  return (
    <div className="mx-auto max-w-6xl space-y-12 px-8 py-10">
      <div className="flex items-end justify-between gap-6">
        <PageHeader
          title="Controls and access"
          description="What Calipa checks, against which accounts and tariff, and what you are entitled to audit. These come from versioned files; a change ships with a release and is recorded in every report that used it."
        />
        <ScalesArt className="hidden shrink-0 lg:block" />
      </div>

      {reference.isError ? (
        <ErrorState error={reference.error} onRetry={() => reference.refetch()} />
      ) : (
        <>
          <Section title="Your access" description="From the entitlements file. In production this follows your SSO groups.">
            {me.data ? <Access me={me.data} /> : <Skeleton className="h-32 w-full rounded-xl" />}
          </Section>

          <Section title="Objectives" description="An audit request is read as one of these. Each runs a fixed set of controls.">
            {reference.data ? <Objectives ref_={reference.data} me={me.data} /> : <Skeleton className="h-48 w-full rounded-xl" />}
          </Section>

          <Section
            title="Controls"
            description="Each control is a rule the reconciliation runs on every transfer. A failure becomes an exception of one of the listed types."
            aside={
              <label className="relative flex items-center">
                <Search className="pointer-events-none absolute left-2.5 size-4 text-ink-subtle" />
                <span className="sr-only">Filter controls</span>
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Escape" && setQ("")}
                  placeholder="Filter controls or exception types"
                  className="h-8 w-72 rounded-lg border border-line bg-canvas/60 pr-7 pl-8 text-small outline-none placeholder:text-ink-subtle focus-visible:border-brand focus-visible:bg-surface focus-visible:ring-2 focus-visible:ring-ring/25"
                />
                {q && (
                  <button onClick={() => setQ("")} aria-label="Clear filter" className="absolute right-1.5 grid size-5 place-items-center rounded text-ink-subtle hover:text-ink">
                    <X className="size-3.5" />
                  </button>
                )}
              </label>
            }
          >
            <div className="overflow-hidden rounded-xl border border-line">
              <div className="grid grid-cols-[120px_minmax(0,1.2fr)_minmax(0,1.5fr)] gap-4 border-b border-line bg-canvas/40 px-5 py-2 text-caption font-medium text-ink-subtle">
                <span>Control</span>
                <span>Checks that</span>
                <span>Raises</span>
              </div>
              {!reference.data ? (
                <div className="space-y-3 p-5">
                  {Array.from({ length: 4 }, (_, i) => (
                    <Skeleton key={i} className="h-5 w-full" />
                  ))}
                </div>
              ) : controls.length === 0 ? (
                <EmptyState art={NoMatchArt} title="No control matches" body={`Nothing has “${q}” in its id, name or exception types.`} />
              ) : (
                <ul className="divide-y divide-line">
                  {controls.map((c) => (
                    <li
                      key={c.control_id}
                      id={c.control_id}
                      className="grid scroll-mt-24 grid-cols-[120px_minmax(0,1.2fr)_minmax(0,1.5fr)] items-start gap-4 px-5 py-4 transition-colors duration-700 data-[flash=true]:bg-signal-soft"
                    >
                      <span className="font-mono text-small font-medium text-ink">{c.control_id}</span>
                      <div className="space-y-1.5">
                        <div className="text-body text-ink">{c.name}</div>
                        <div className="flex flex-wrap gap-1">
                          {c.objectives.map((o) => (
                            <span key={o} className="rounded bg-surface-3 px-1.5 py-px text-[11px] text-ink-muted">
                              {objectiveLabel(o)}
                            </span>
                          ))}
                        </div>
                      </div>
                      <ul className="space-y-1">
                        {c.exception_types.map((t) => (
                          <li key={t} className="flex flex-wrap items-baseline gap-x-2 text-small">
                            <span className="text-ink">{EXCEPTION_TYPES[t] ?? t}</span>
                            <span className="font-mono text-[11px] text-ink-subtle">{t}</span>
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Section>

          <div className="grid gap-12 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <Section title="Accounts" description="The GL accounts the FCY transfers post to.">
              <div className="overflow-hidden rounded-xl border border-line">
                {!reference.data ? (
                  <Skeleton className="m-5 h-20" />
                ) : (
                  <ul className="divide-y divide-line">
                    {reference.data.accounts.map((a) => (
                      <li key={a.account_number} className="grid grid-cols-[110px_130px_minmax(0,1fr)] items-baseline gap-4 px-5 py-3">
                        <span className="text-small font-medium">{ACCOUNT_ROLES[a.role] ?? a.role}</span>
                        <RefId value={a.account_number} keep={10} className="-ml-1.5" />
                        <span className="text-small text-ink-muted text-pretty">{a.description}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Section>

            <Section title="Tariff" description="What each transfer should be charged.">
              {!reference.data ? (
                <Skeleton className="h-28 w-full rounded-xl" />
              ) : (
                <dl className="divide-y divide-line overflow-hidden rounded-xl border border-line">
                  <div className="flex items-baseline justify-between gap-4 px-5 py-3">
                    <dt className="text-small text-ink-muted">Flat charge</dt>
                    <dd>
                      <Amount value={reference.data.tariff.flat_charge.amount} currency={reference.data.tariff.flat_charge.currency} />
                    </dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-4 px-5 py-3">
                    <dt className="text-small text-ink-muted">VAT, on the principal</dt>
                    <dd className="figures font-mono text-small">{pct(reference.data.tariff.vat_rate)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-4 px-5 py-3">
                    <dt className="text-small text-ink-muted">Rounding</dt>
                    <dd className="font-mono text-caption">{reference.data.tariff.rounding}</dd>
                  </div>
                </dl>
              )}
            </Section>
          </div>

          {reference.data && (
            <p className="border-t border-line pt-4 font-mono text-[11px] text-ink-subtle">
              {Object.entries(reference.data.versions)
                .map(([f, v]) => `${f}.v${v}`)
                .join(" · ")}{" "}
              · pending confirmation by Finance and Ops
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** "0.075" → "7.5%", by moving the decimal point in the string; no floats. */
function pct(rate: string) {
  const [i, f = ""] = rate.split(".");
  const digits = i + f.padEnd(2, "0");
  const whole = digits.slice(0, i.length + 2).replace(/^0+(?=\d)/, "") || "0";
  const rest = digits.slice(i.length + 2).replace(/0+$/, "");
  return `${whole}${rest ? `.${rest}` : ""}%`;
}
