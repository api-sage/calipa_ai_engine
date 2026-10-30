import { cn } from "@/lib/utils";
import { parseAmount } from "@/lib/money";

type Size = "sm" | "md" | "lg" | "figure";

const SIZE: Record<Size, { root: string; minor: string; code: string }> = {
  sm: { root: "text-small", minor: "", code: "text-caption" },
  md: { root: "text-body", minor: "", code: "text-caption" },
  lg: { root: "text-display font-medium tracking-tight", minor: "text-ink-muted", code: "text-title" },
  figure: { root: "text-figure font-medium tracking-[-0.02em]", minor: "text-ink-muted", code: "text-heading" },
};

/**
 * A money amount, exact to the digit. Always carries its currency code, because a
 * bare "$" is ambiguous in a multi-currency ledger. `signed` shows "+" on positive
 * values (for differences); colour is left to the caller, since a negative amount
 * is not necessarily bad.
 */
export function Amount({
  value,
  currency,
  size = "md",
  signed = false,
  hideCode = false,
  className,
}: {
  value: string;
  currency: string;
  size?: Size;
  signed?: boolean;
  hideCode?: boolean;
  className?: string;
}) {
  const p = parseAmount(value, currency);
  const s = SIZE[size];
  const sign = p.negative ? "−" : signed && !p.zero ? "+" : "";
  return (
    <span
      className={cn("figures inline-flex items-baseline gap-[0.3em] whitespace-nowrap", s.root, className)}
      aria-label={`${p.negative ? "minus " : ""}${p.integer}${p.fraction ? `.${p.fraction}` : ""} ${currency}`}
    >
      <span>
        {sign}
        {p.integer}
        {p.fraction && <span className={s.minor}>.{p.fraction}</span>}
      </span>
      {!hideCode && <span className={cn("font-normal text-ink-subtle tracking-normal", s.code)}>{currency}</span>}
    </span>
  );
}
