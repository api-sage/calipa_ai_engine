"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

function middle(value: string, keep: number) {
  return value.length <= keep * 2 + 1 ? value : `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

/** A transfer id, run id or hash: shortened in the middle, full on hover, one click to copy. */
export function RefId({ value, keep = 6, className }: { value: string; keep?: number; className?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard refused (permissions); the full value is still in the tooltip to select.
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={copy}
          aria-label={`Copy ${value}`}
          className={cn(
            "group figures inline-flex h-6 items-center gap-1.5 rounded-md px-1.5 font-mono text-caption text-ink-muted transition-colors hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50 outline-none",
            className,
          )}
        >
          {middle(value, keep)}
          <span className="relative size-3.5">
            <AnimatePresence initial={false} mode="popLayout">
              {copied ? (
                <motion.span key="ok" initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 text-up">
                  <Check className="size-3.5" strokeWidth={2.5} />
                </motion.span>
              ) : (
                <motion.span key="copy" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0">
                  <Copy className="size-3.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
                </motion.span>
              )}
            </AnimatePresence>
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent className="font-mono figures">{copied ? "Copied" : value}</TooltipContent>
    </Tooltip>
  );
}
