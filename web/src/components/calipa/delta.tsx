import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A change between expected and actual. Direction comes from the sign; whether
 * that direction is good is the caller's call (`goodWhen`): more fee income is
 * good, more exceptions are not. `neutral` drops the judgement entirely, which is
 * the default for audit differences: Calipa reports, the reviewer judges.
 */
export function Delta({
  value,
  suffix,
  goodWhen = "neutral",
  className,
}: {
  value: string; // signed decimal, e.g. "0.50", "-2.3"
  suffix?: string; // "%", "USD"
  goodWhen?: "up" | "down" | "neutral";
  className?: string;
}) {
  const n = Number(value);
  const dir = n > 0 ? "up" : n < 0 ? "down" : "flat";
  const tone =
    dir === "flat" || goodWhen === "neutral"
      ? "bg-surface-2 text-ink-muted"
      : dir === goodWhen
        ? "bg-up-soft text-up"
        : "bg-down-soft text-down";
  const Icon = dir === "up" ? ArrowUpRight : dir === "down" ? ArrowDownRight : Minus;
  const abs = value.replace(/^[-+]/, "");
  return (
    <span
      className={cn(
        "figures inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-caption font-medium whitespace-nowrap",
        tone,
        className,
      )}
    >
      <Icon className="size-3.5" strokeWidth={2.25} aria-hidden />
      {dir === "down" ? "−" : dir === "up" ? "+" : ""}
      {abs}
      {suffix && <span className="opacity-75">{suffix === "%" ? "%" : ` ${suffix}`}</span>}
    </span>
  );
}
