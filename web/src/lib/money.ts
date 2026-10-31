// Amounts arrive from the API as decimal strings ("3753.93", "-0.5"). They are
// formatted from the string, never through a JS number, so no digit is lost or
// rounded on the way to the screen.

const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

/** Minor-unit digits per currency (ISO 4217). Default 2. */
const MINOR_DIGITS: Record<string, number> = { JPY: 0, KRW: 0, KWD: 3, BHD: 3 };

export function minorDigits(currency: string): number {
  return MINOR_DIGITS[currency.toUpperCase()] ?? 2;
}

export type ParsedAmount = {
  negative: boolean;
  integer: string; // grouped, e.g. "3,753"
  fraction: string; // padded to the currency's digits, e.g. "93"
  zero: boolean;
};

export function parseAmount(value: string, currency: string): ParsedAmount {
  const m = DECIMAL.exec(value.trim());
  if (!m) throw new Error(`not a decimal amount: ${value}`);
  const [, sign, int, frac = ""] = m;
  const digits = minorDigits(currency);
  if (frac.replace(/0+$/, "").length > digits) {
    // More precision than the currency has: show it all rather than round money.
    return build(sign === "-", int, frac);
  }
  return build(sign === "-", int, frac.padEnd(digits, "0").slice(0, digits));
}

function build(negative: boolean, int: string, fraction: string): ParsedAmount {
  const trimmed = int.replace(/^0+(?=\d)/, "");
  const zero = /^0*$/.test(trimmed + fraction);
  return {
    negative: negative && !zero,
    integer: trimmed.replace(/\B(?=(\d{3})+(?!\d))/g, ","),
    fraction,
    zero,
  };
}

/** "−3,753.93" with a true minus sign; used where a plain string is needed. */
export function formatAmount(value: string, currency: string): string {
  const p = parseAmount(value, currency);
  const body = p.fraction ? `${p.integer}.${p.fraction}` : p.integer;
  return `${p.negative ? "−" : ""}${body}`;
}

/** Compact form for dense places (charts, chips): 8.28M, 132.6K. Display only. */
export function formatCompact(value: string): string {
  const n = Number(value);
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(n);
}

/** Exact sum of decimal strings, e.g. ["0.1", "0.2"] -> "0.3". Signs allowed. */
export function sumAmounts(values: string[]): string {
  const parts = values.map((v) => {
    const m = DECIMAL.exec(v.trim());
    if (!m) throw new Error(`not a decimal amount: ${v}`);
    return { negative: m[1] === "-", int: m[2], frac: m[3] ?? "" };
  });
  const scale = Math.max(0, ...parts.map((p) => p.frac.length));
  let total = BigInt(0);
  for (const p of parts) {
    const n = BigInt(p.int + p.frac.padEnd(scale, "0"));
    total += p.negative ? -n : n;
  }
  const negative = total < BigInt(0);
  const digits = (negative ? -total : total).toString().padStart(scale + 1, "0");
  const int = digits.slice(0, digits.length - scale);
  const frac = digits.slice(digits.length - scale);
  return `${negative ? "-" : ""}${int}${scale ? `.${frac}` : ""}`;
}

/** Exact a − b for decimal strings. */
export function subtractAmounts(a: string, b: string): string {
  return sumAmounts([a, b.startsWith("-") ? b.slice(1) : `-${b}`]);
}
