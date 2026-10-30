import { cn } from "@/lib/utils";

/*
  Spot illustrations for empty, error and edge states.

  One drawing language: 1.5px ink lines on a 120 × 96 grid, paper fills, a soft
  ground shadow, and a single signal-orange dot, the same dot as the one over the
  i in the wordmark. The dot marks where the eye should go.
*/

export type Art = (props: { className?: string }) => React.JSX.Element;

function Frame({ className, children, label }: { className?: string; children: React.ReactNode; label?: string }) {
  return (
    <svg
      viewBox="0 0 120 96"
      fill="none"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn("h-24 w-30 text-ink-subtle", className)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <ellipse cx="60" cy="86" rx="38" ry="4" className="fill-ink/[0.06]" stroke="none" />
      {children}
    </svg>
  );
}

const paper = "fill-surface stroke-current";
const dot = "fill-signal";

/** An open ledger with nothing posted yet. No audits. */
export const LedgerArt: Art = ({ className }) => (
  <Frame className={className}>
    <path d="M60 24c-9-5-22-6-34-3v56c12-3 25-2 34 3 9-5 22-6 34-3V21c-12-3-25-2-34 3Z" className={paper} />
    <path d="M60 24v56" className="stroke-current" />
    <path d="M34 34h16M34 42h18M34 50h12" className="stroke-current opacity-50" />
    <path d="M68 34h18M68 42h12" className="stroke-current opacity-50" strokeDasharray="2 3" />
    <circle cx="86" cy="50" r="3.5" className={dot} />
  </Frame>
);

/** An empty tray with a tick. Nothing waiting for review. */
export const InboxArt: Art = ({ className }) => (
  <Frame className={className}>
    <path d="M24 50 36 26h48l12 24v24a4 4 0 0 1-4 4H28a4 4 0 0 1-4-4V50Z" className={paper} />
    <path d="M24 50h20l4 8h24l4-8h20" className="stroke-current" />
    <path d="m51 40 6 6 12-12" className="stroke-brand" strokeWidth={2} />
    <circle cx="84" cy="20" r="3.5" className={dot} />
  </Frame>
);

/** A magnifier over a list with a gap in it. No search results. */
export const NoMatchArt: Art = ({ className }) => (
  <Frame className={className}>
    <rect x="22" y="18" width="52" height="62" rx="5" className={paper} />
    <path d="M31 31h30M31 40h22M31 49h26M31 58h16" className="stroke-current opacity-50" />
    <circle cx="74" cy="54" r="15" className={paper} />
    <path d="m85 65 11 11" className="stroke-current" strokeWidth={3} />
    <path d="M68 54h12" className="stroke-current" strokeDasharray="2 3" />
    <circle cx="74" cy="54" r="2.5" className={dot} />
  </Frame>
);

/** Two chain links pulled apart. Something failed, or the chain broke. */
export const BrokenArt: Art = ({ className }) => (
  <Frame className={className}>
    <rect x="18" y="38" width="36" height="20" rx="10" transform="rotate(-18 36 48)" className={paper} />
    <rect x="66" y="38" width="36" height="20" rx="10" transform="rotate(-18 84 48)" className={paper} />
    <path d="m57 34 2-7M63 38l6-4M55 60l-3 6" className="stroke-current" />
    <circle cx="60" cy="48" r="3.5" className={dot} />
  </Frame>
);

/** A folded page and a compass point. Not found. */
export const LostArt: Art = ({ className }) => (
  <Frame className={className}>
    <path d="M30 16h40l14 14v50H30V16Z" className={paper} />
    <path d="M70 16v14h14" className="stroke-current" />
    <circle cx="57" cy="52" r="14" className="stroke-current opacity-60" strokeDasharray="2 3" />
    <path d="m52 57 3-8 8-3-3 8-8 3Z" className="fill-ink/10 stroke-current" />
    <circle cx="57" cy="52" r="2.5" className={dot} />
  </Frame>
);

/** An hourglass with the sand stopped. A run that has stalled. */
export const StalledArt: Art = ({ className }) => (
  <Frame className={className}>
    <path d="M42 14h36M42 82h36" className="stroke-current" strokeWidth={2} />
    <path d="M46 14c0 18 14 22 14 34s-14 16-14 34h28c0-18-14-22-14-34s14-16 14-34H46Z" className={paper} />
    <path d="M52 76c2-6 6-8 8-8s6 2 8 8H52Z" className="fill-ink/15" stroke="none" />
    <path d="M55 30h10" className="stroke-current opacity-50" />
    <circle cx="60" cy="48" r="3" className={dot} />
  </Frame>
);

/** A shield with a bar across it. Not permitted. */
export const DeniedArt: Art = ({ className }) => (
  <Frame className={className}>
    <path d="M60 12 34 22v22c0 18 11 30 26 36 15-6 26-18 26-36V22L60 12Z" className={paper} />
    <path d="M48 46h24" className="stroke-current" strokeWidth={2.5} />
    <circle cx="86" cy="22" r="3.5" className={dot} />
  </Frame>
);

/** A balance, level. The controls Calipa checks against. */
export const ScalesArt: Art = ({ className }) => (
  <Frame className={className}>
    <path d="M60 18v58M44 78h32" className="stroke-current" strokeWidth={2} />
    <path d="M30 28h60" className="stroke-current" />
    <path d="m30 28-10 22h20L30 28ZM90 28l-10 22h20L90 28Z" className="stroke-current opacity-60" />
    <path d="M20 50a10 5 0 0 0 20 0H20ZM80 50a10 5 0 0 0 20 0H80Z" className={paper} />
    <circle cx="60" cy="18" r="3.5" className={dot} />
  </Frame>
);

/** A speech bubble with a question. Calipa needs an answer. */
export const QuestionArt: Art = ({ className }) => (
  <Frame className={className}>
    <path d="M24 22h72a4 4 0 0 1 4 4v36a4 4 0 0 1-4 4H54L40 78V66H24a4 4 0 0 1-4-4V26a4 4 0 0 1 4-4Z" className={paper} />
    <path d="M53 38a7 7 0 1 1 9 7c-1.5.5-2 1.5-2 3v1" className="stroke-current" strokeWidth={2} />
    <circle cx="60" cy="56" r="2.5" className={dot} />
  </Frame>
);

/** A clean sheet with a seal. Approved, or nothing to report. */
export const SealedArt: Art = ({ className }) => (
  <Frame className={className}>
    <rect x="30" y="14" width="52" height="66" rx="4" className={paper} />
    <path d="M40 28h32M40 36h24M40 44h28" className="stroke-current opacity-50" />
    <circle cx="74" cy="64" r="12" className="fill-brand-soft stroke-brand" />
    <path d="m69 64 4 4 7-8" className="stroke-brand" strokeWidth={2} />
    <circle cx="88" cy="50" r="3" className={dot} />
  </Frame>
);
