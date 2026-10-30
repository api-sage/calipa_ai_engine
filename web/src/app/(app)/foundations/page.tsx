import type { Metadata } from "next";
import { ArrowRight, Check, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Amount } from "@/components/calipa/amount";
import { Delta } from "@/components/calipa/delta";
import { LiveIndicator } from "@/components/calipa/live-indicator";
import { Provenance } from "@/components/calipa/provenance";
import { RefId } from "@/components/calipa/ref-id";
import { RunStatePill } from "@/components/calipa/run-state-pill";
import { SeverityMark } from "@/components/calipa/severity";
import { Timestamp } from "@/components/calipa/timestamp";
import { RUN_STATES } from "@/lib/run-state";
import {
  BrokenArt,
  DeniedArt,
  InboxArt,
  LedgerArt,
  LostArt,
  NoMatchArt,
  QuestionArt,
  ScalesArt,
  SealedArt,
  StalledArt,
} from "@/components/calipa/illustrations";
import { LogoMark, Wordmark } from "@/components/shell/logo";
import { CheckCounterDemo, DialogDemo, LoadingButtonDemo, RunStateDemo } from "./demos";

const ART = [
  { Art: LedgerArt, name: "Ledger", use: "No audits yet" },
  { Art: InboxArt, name: "Inbox", use: "Nothing to review" },
  { Art: NoMatchArt, name: "No match", use: "Search or filter is empty" },
  { Art: QuestionArt, name: "Question", use: "Calipa needs an answer" },
  { Art: StalledArt, name: "Stalled", use: "No progress for a while" },
  { Art: DeniedArt, name: "Denied", use: "Outside the user's access" },
  { Art: BrokenArt, name: "Broken", use: "Errors, broken chain" },
  { Art: LostArt, name: "Lost", use: "Page not found" },
  { Art: SealedArt, name: "Sealed", use: "Approved, nothing to report" },
  { Art: ScalesArt, name: "Scales", use: "Controls" },
];

const BRAND = [
  { hex: "#FAF9F5", name: "Paper" },
  { hex: "#F4F1EA", name: "Paper 2" },
  { hex: "#EAE7DF", name: "Paper 3" },
  { hex: "#E5E2DA", name: "Rule" },
  { hex: "#C7C3B8", name: "Rule strong" },
  { hex: "#151514", name: "Ink" },
  { hex: "#6C6963", name: "Ink muted" },
  { hex: "#2D6B50", name: "Calipa green" },
  { hex: "#C85A30", name: "Signal orange" },
  { hex: "#B7872D", name: "Ochre" },
];

export const metadata: Metadata = { title: "Foundations" };

// Real values from run 782eef78… (P3 eval case mix-s1-plain).
const TRANSFER = "c74803e3-1ba1-4215-8228-3d15a9ec0806";
const RUN = "782eef78-47ad-44ce-b14b-fa4f8f69a1f5";
const SNAPSHOT = "6b610b9f32210edf971d50e505473596ff48e060a82405aeeacd7808375ea872";

const SWATCHES: { group: string; tokens: { name: string; role: string }[] }[] = [
  {
    group: "Surfaces and ink",
    tokens: [
      { name: "canvas", role: "Page" },
      { name: "surface", role: "Cards, panels" },
      { name: "surface-2", role: "Hover, wells" },
      { name: "surface-3", role: "Pressed, tracks" },
      { name: "line", role: "Dividers" },
      { name: "ink", role: "Primary text" },
      { name: "ink-muted", role: "Secondary text" },
      { name: "ink-subtle", role: "Labels, units" },
    ],
  },
  {
    group: "Meaning",
    tokens: [
      { name: "brand", role: "Interactive, focus" },
      { name: "signal", role: "The dot; sparingly" },
      { name: "up", role: "Increase, passed, done" },
      { name: "down", role: "Decrease, failed" },
      { name: "attention", role: "Needs a person" },
      { name: "ai", role: "Written by Claude" },
      { name: "verified", role: "Checked by code" },
    ],
  },
  {
    group: "Severity (separate from up/down)",
    tokens: [
      { name: "sev-high", role: "High, critical" },
      { name: "sev-medium", role: "Medium" },
      { name: "sev-low", role: "Low" },
    ],
  },
];

const TYPE: { cls: string; name: string; spec: string; sample: React.ReactNode }[] = [
  { cls: "font-serif text-figure font-medium figures", name: "Figure · Newsreader", spec: "44 / 48 · 500", sample: "8,282,979.90" },
  { cls: "font-serif text-display font-medium", name: "Display · Newsreader", spec: "32 / 40 · 500", sample: "Fee and VAT legs, October" },
  { cls: "font-serif text-heading font-medium", name: "Heading · Newsreader", spec: "24 / 32 · 500", sample: "Findings" },
  { cls: "font-serif text-title font-medium", name: "Title · Newsreader", spec: "20 / 28 · 500", sample: "G1: charge amount mismatch" },
  { cls: "text-lead", name: "Lead", spec: "16 / 24 · 400", sample: "5 transfers checked; 3 exceptions within scope." },
  { cls: "text-body", name: "Body", spec: "14 / 22 · 400", sample: "The charge entry was posted at 2.50 while the tariff flat charge is 2.00." },
  { cls: "text-small", name: "Small", spec: "13 / 20 · 400", sample: "Table cells and dense lists" },
  { cls: "text-caption text-ink-muted", name: "Caption", spec: "12 / 16 · 400", sample: "Labels, units, timestamps" },
  { cls: "font-mono text-small figures", name: "Mono · JetBrains", spec: "13 / 20 · 400", sample: "R-FEE · EX-1b19e57266bccb3d" },
];

function Section({ id, title, note, children }: { id: string; title: string; note: string; children: React.ReactNode }) {
  return (
    <section id={id} className="grid gap-6 border-t border-line pt-10 md:grid-cols-[220px_1fr] md:gap-10">
      <div className="space-y-1.5">
        <h2 className="font-serif text-heading font-medium">{title}</h2>
        <p className="text-small text-ink-muted text-pretty">{note}</p>
      </div>
      <div className="min-w-0 space-y-8">{children}</div>
    </section>
  );
}

function Specimen({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className="space-y-3">
      <div className="text-caption font-medium tracking-wide text-ink-subtle uppercase">{label}</div>
      <div className={className}>{children}</div>
    </div>
  );
}

function Panel({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-xl bg-surface p-5 shadow-raised ${className ?? ""}`}>{children}</div>;
}

export default function Foundations() {
  return (
    <div>
      <main className="mx-auto max-w-6xl space-y-14 px-6 pt-12 pb-24">
        <div className="max-w-2xl space-y-3">
          <h1 className="font-serif text-display font-medium text-balance">Foundations</h1>
          <p className="text-lead text-ink-muted text-pretty">
            Tokens and atoms for Calipa, in every state, before any screen is built. Values on this page come from a real
            run: transfer {TRANSFER.slice(0, 8)}… was charged 2.50 USD against a 2.00 USD tariff.
          </p>
        </div>

        <Section id="brand" title="Brand" note="The wordmark is Newsreader; the dot over the i is the one place orange always appears.">
          <Specimen label="Wordmark and mark" className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
            <Panel className="grid place-items-center py-10">
              <Wordmark className="h-14" />
            </Panel>
            <Panel className="grid place-items-center px-10">
              <LogoMark className="size-14 rounded-[15px]" />
            </Panel>
            <div className="grid place-items-center rounded-xl bg-brand px-10">
              <span className="grid size-14 place-items-center rounded-[15px] bg-[#faf9f5]">
                <LogoMark className="size-12 rounded-[12px]" />
              </span>
            </div>
          </Specimen>
          <Specimen label="Profile (color_profile.pdf)" className="grid grid-cols-5 gap-2">
            {BRAND.map((b) => (
              <div key={b.hex} className="space-y-1.5">
                <div className="h-10 rounded-md ring-1 ring-ink/10 ring-inset" style={{ background: b.hex }} />
                <div className="text-caption">{b.name}</div>
                <div className="font-mono text-[11px] text-ink-subtle">{b.hex}</div>
              </div>
            ))}
          </Specimen>
        </Section>

        <Section id="colour" title="Colour" note="Paper neutrals, one interactive green, orange as a signal only. Gains and losses never share a scale with severity.">
          {SWATCHES.map((g) => (
            <Specimen key={g.group} label={g.group} className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {g.tokens.map((t) => (
                <div key={t.name} className="space-y-2">
                  <div className="h-14 rounded-lg ring-1 ring-line ring-inset" style={{ background: `var(--${t.name})` }} />
                  <div>
                    <div className="font-mono text-caption">{t.name}</div>
                    <div className="text-caption text-ink-muted">{t.role}</div>
                  </div>
                </div>
              ))}
            </Specimen>
          ))}
        </Section>

        <Section id="type" title="Type" note="Newsreader for headings and display, Geist for the interface, JetBrains Mono for identifiers and figures. Every figure uses tabular digits so columns line up.">
          <div className="divide-y divide-line">
            {TYPE.map((t) => (
              <div key={t.name} className="grid items-baseline gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <div>
                  <div className="text-small font-medium">{t.name}</div>
                  <div className="figures text-caption text-ink-subtle">{t.spec}</div>
                </div>
                <div className={`${t.cls} min-w-0 truncate`}>{t.sample}</div>
              </div>
            ))}
          </div>
        </Section>

        <Section id="money" title="Money" note="Exact to the digit, formatted from the string. Always with a currency code. Minor units recede at large sizes.">
          <Panel className="space-y-6">
            <Specimen label="Figure">
              <Amount value="8282979.9" currency="NGN" size="figure" />
            </Specimen>
            <div className="grid gap-6 sm:grid-cols-3">
              <Specimen label="Large">
                <Amount value="3753.93" currency="USD" size="lg" />
              </Specimen>
              <Specimen label="Negative">
                <Amount value="-0.5" currency="USD" size="lg" />
              </Specimen>
              <Specimen label="Zero-decimal currency">
                <Amount value="120000" currency="JPY" size="lg" />
              </Specimen>
            </div>
            <div className="grid gap-6 sm:grid-cols-3">
              <Specimen label="Body">
                <Amount value="4635.53" currency="GBP" />
              </Specimen>
              <Specimen label="Signed difference">
                <Amount value="0.5" currency="USD" signed />
              </Specimen>
              <Specimen label="Extra precision kept">
                <Amount value="1.4725" currency="GBP" />
              </Specimen>
            </div>
          </Panel>
          <Specimen label="Change" className="flex flex-wrap gap-2">
            <Delta value="0.50" suffix="USD" />
            <Delta value="-0.50" suffix="USD" />
            <Delta value="12.4" suffix="%" goodWhen="up" />
            <Delta value="-2.3" suffix="%" goodWhen="up" />
            <Delta value="3" goodWhen="down" />
            <Delta value="0" suffix="%" />
          </Specimen>
          <p className="max-w-prose text-small text-ink-muted">
            Audit differences are neutral by default: Calipa reports the difference, the reviewer judges it. Green and red
            appear only where a direction is clearly good or bad.
          </p>
        </Section>

        <Section id="states" title="Run states" note="Seventeen states in five families. Working states pulse in green; states that need a person are ochre.">
          <Specimen label="Live transition">
            <RunStateDemo />
          </Specimen>
          <Specimen label="All states" className="flex flex-wrap gap-2">
            {RUN_STATES.map((s) => (
              <RunStatePill key={s} state={s} />
            ))}
          </Specimen>
          <Specimen label="Stream" className="flex flex-wrap gap-6">
            <LiveIndicator status="live" />
            <LiveIndicator status="reconnecting" />
            <LiveIndicator status="offline" />
          </Specimen>
        </Section>

        <Section id="trust" title="Trust" note="Every sentence and figure says where it came from. This is always visible, never hidden behind a hover.">
          <Panel className="space-y-5">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <span className="font-serif text-title font-medium">G1</span>
                <SeverityMark severity="high" />
              </div>
              <RefId value={TRANSFER} />
            </div>
            <p className="max-w-prose text-body text-pretty">
              The charge entry was posted at <Amount value="2.5" currency="USD" /> while the tariff flat charge is{" "}
              <Amount value="2" currency="USD" />; the excess accounts for the transient shortfall of{" "}
              <Amount value="-0.5" currency="USD" />.
            </p>
            <Provenance kind="ai-checked" checker="V2" />
            <div className="space-y-2 border-t border-line pt-4">
              <p className="text-body text-ink-subtle line-through decoration-down/60">
                The customer intended to pay the higher charge.
              </p>
              <Provenance kind="ai-dropped" checker="V2" />
            </div>
            <div className="border-t border-line pt-4">
              <Provenance kind="code" />
            </div>
          </Panel>
          <Specimen label="Severity" className="flex flex-wrap gap-6">
            <SeverityMark severity="critical" />
            <SeverityMark severity="high" />
            <SeverityMark severity="medium" />
            <SeverityMark severity="low" />
          </Specimen>
          <Specimen label="Identifiers" className="flex flex-wrap items-center gap-3">
            <RefId value={RUN} keep={8} />
            <RefId value={SNAPSHOT} />
            <RefId value="EX-1b19e57266bccb3d" keep={10} />
          </Specimen>
          <Specimen label="Time (hover for bank time)" className="flex flex-wrap gap-6 text-small">
            <Timestamp iso={new Date(Date.now() - 42_000).toISOString()} />
            <Timestamp iso={new Date(Date.now() - 81_000).toISOString()} />
            <Timestamp iso="2026-10-27T17:08:56.537745+01:00" />
          </Specimen>
        </Section>

        <Section id="actions" title="Actions" note="Ink is the primary action. Green is for the one action that starts work. Orange is never a button. Loading keeps the button's width.">
          <Specimen label="Variants" className="flex flex-wrap gap-3">
            <Button>Approve report</Button>
            <Button variant="brand">
              <Plus /> New audit
            </Button>
            <Button variant="outline">Send back</Button>
            <Button variant="secondary">Export</Button>
            <Button variant="ghost">Cancel</Button>
            <Button variant="destructive">
              <X /> Reject
            </Button>
            <Button variant="link">
              View evidence <ArrowRight />
            </Button>
          </Specimen>
          <Specimen label="States" className="flex flex-wrap items-center gap-3">
            <LoadingButtonDemo />
            <Button loading>Approving</Button>
            <Button disabled>Approve report</Button>
            <Button variant="outline" disabled>
              <Check /> Approved
            </Button>
          </Specimen>
          <Specimen label="Sizes" className="flex flex-wrap items-center gap-3">
            <Button size="xs">Extra small</Button>
            <Button size="sm">Small</Button>
            <Button>Default</Button>
            <Button size="lg">Large</Button>
          </Specimen>
        </Section>

        <Section id="dialogs" title="Dialogs" note="Confirm before anything that cannot be taken back or that spends money. Errors that block a decision open a dialog; the rest are toasts.">
          <DialogDemo />
        </Section>

        <Section id="art" title="Illustrations" note="One line weight, paper fills, and the orange dot where the eye should go. Used for empty, edge and error states.">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {ART.map(({ Art, name, use }) => (
              <div key={name} className="flex flex-col items-center gap-2 rounded-xl bg-surface p-4 text-center shadow-raised">
                <Art />
                <div>
                  <div className="text-small font-medium">{name}</div>
                  <div className="text-caption text-ink-muted">{use}</div>
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section id="motion" title="Motion" note="Counts may animate; money never does, so a figure is never on screen in a wrong in-between value.">
          <Panel>
            <CheckCounterDemo />
          </Panel>
          <div className="grid gap-3 font-mono text-caption text-ink-muted sm:grid-cols-3">
            <div>fast · 120ms · hover, press</div>
            <div>base · 220ms · state change</div>
            <div>slow · 350ms · layout, panels</div>
          </div>
        </Section>

        <Section id="loading" title="Loading" note="Skeletons take the exact shape of what they stand for, so nothing jumps when data arrives.">
          <Panel className="space-y-4">
            <div className="flex items-center justify-between">
              <Skeleton className="h-6 w-28 rounded-full" />
              <Skeleton className="h-5 w-32" />
            </div>
            <Skeleton className="h-12 w-72" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-full max-w-prose" />
              <Skeleton className="h-4 w-4/5 max-w-prose" />
            </div>
          </Panel>
        </Section>
      </main>
    </div>
  );
}
