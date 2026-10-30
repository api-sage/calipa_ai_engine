import { Braces, CircleSlash, ShieldCheck, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Where a piece of the report came from. This is the trust story of the product,
 * so it is always visible, never only on hover.
 * - ai-checked: Claude wrote it and a code checker passed it (V1/V2/V3)
 * - ai-dropped: Claude claimed it, the checker could not trace it, so it is shown struck out
 * - code: computed by code from the data; no model involved
 */
export function Provenance({
  kind,
  checker,
  className,
}: {
  kind: "ai-checked" | "ai-dropped" | "code";
  checker?: "V1" | "V2" | "V3";
  className?: string;
}) {
  if (kind === "code") {
    return (
      <span className={cn("inline-flex items-center gap-1 text-caption text-ink-muted", className)}>
        <Braces className="size-3.5" aria-hidden />
        Computed from the data
      </span>
    );
  }
  if (kind === "ai-dropped") {
    return (
      <span className={cn("inline-flex items-center gap-1 text-caption text-down", className)}>
        <CircleSlash className="size-3.5" aria-hidden />
        Dropped by {checker ?? "the checker"}: no evidence
      </span>
    );
  }
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-caption", className)}>
      <span className="inline-flex items-center gap-1 text-ai">
        <Sparkles className="size-3.5" aria-hidden />
        Written by Claude
      </span>
      <span className="text-ink-subtle" aria-hidden>
        ·
      </span>
      <span className="inline-flex items-center gap-1 text-verified">
        <ShieldCheck className="size-3.5" aria-hidden />
        Checked by {checker ?? "code"}
      </span>
    </span>
  );
}
