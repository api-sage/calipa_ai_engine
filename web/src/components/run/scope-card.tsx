import type { ReportDetail } from "@/lib/api/client";
import { ScopeDays } from "./charts";

const dateFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

function period(start: string, end: string) {
  const a = dateFmt.format(new Date(`${start}T00:00:00Z`));
  const b = dateFmt.format(new Date(`${end}T00:00:00Z`));
  return a === b ? a : `${a} – ${b}`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[96px_1fr] gap-3 py-1.5">
      <dt className="text-caption text-ink-subtle">{label}</dt>
      <dd className="min-w-0 text-small text-ink">{children}</dd>
    </div>
  );
}

/** What the run was approved to audit, as P1 read it and the policy check allowed it. */
export function ScopeCard({ scope, days = [] }: { scope: ReportDetail["scope"]; days?: ReportDetail["days"] }) {
  const all = (xs: string[]) => (xs.length ? xs.join(", ") : <span className="text-ink-muted">All</span>);
  return (
    <section className="space-y-2">
      <h3 className="text-small font-medium">Approved scope</h3>
      <dl className="divide-y divide-line">
        <Row label="Objective">{scope.objective.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}</Row>
        <Row label="Period">
          <span className="figures">{period(scope.period_start, scope.period_end)}</span>
          <span className="block text-caption text-ink-subtle">{scope.timezone}</span>
        </Row>
        {days.length > 0 && (
          <div className="py-3">
            <ScopeDays days={days} />
          </div>
        )}
        <Row label="Currencies">{all(scope.currencies)}</Row>
        <Row label="Controls">{all(scope.control_ids)}</Row>
        {scope.transaction_references.length > 0 && <Row label="References">{scope.transaction_references.join(", ")}</Row>}
        <Row label="Approved by">{scope.approved_by}</Row>
      </dl>
      {scope.assumptions.length > 0 && (
        <div className="rounded-lg bg-attention-soft px-3 py-2 text-caption text-ink">
          <span className="font-medium text-attention">To confirm: </span>
          {scope.assumptions.join(" ")}
        </div>
      )}
    </section>
  );
}
