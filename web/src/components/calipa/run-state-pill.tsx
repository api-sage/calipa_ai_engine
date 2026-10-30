"use client";

import { AnimatePresence, motion } from "motion/react";
import { Ban, Check, CircleAlert, CircleDashed, Hand, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { RUN_STATE_META, type RunFamily, type RunState } from "@/lib/run-state";

const FAMILY_STYLE: Record<RunFamily, string> = {
  working: "bg-brand-soft text-brand-ink",
  "needs-you": "bg-attention-soft text-attention",
  done: "bg-up-soft text-up",
  stopped: "bg-surface-3 text-ink-muted",
  failed: "bg-down-soft text-down",
};

function FamilyGlyph({ family, state }: { family: RunFamily; state: RunState }) {
  if (family === "working") {
    return state === "RECEIVED" ? (
      <CircleDashed className="size-3.5" aria-hidden />
    ) : (
      <span className="relative mx-[3px] inline-flex size-2 rounded-full bg-current animate-pulse-dot" aria-hidden />
    );
  }
  const Icon = { "needs-you": Hand, done: Check, stopped: Ban, failed: state === "REJECTED" ? X : CircleAlert }[family];
  return <Icon className="size-3.5" strokeWidth={2.25} aria-hidden />;
}

export function RunStatePill({ state, className }: { state: RunState; className?: string }) {
  const meta = RUN_STATE_META[state];
  return (
    <motion.span
      layout
      transition={{ type: "spring", duration: 0.35, bounce: 0 }}
      title={meta.hint}
      className={cn(
        "inline-flex h-6 items-center gap-1.5 overflow-hidden rounded-full pl-2 pr-2.5 text-caption font-medium whitespace-nowrap transition-colors duration-300",
        FAMILY_STYLE[meta.family],
        className,
      )}
    >
      <FamilyGlyph family={meta.family} state={state} />
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={state}
          initial={{ opacity: 0, y: 6, filter: "blur(2px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -6, filter: "blur(2px)" }}
          transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
        >
          {meta.label}
        </motion.span>
      </AnimatePresence>
    </motion.span>
  );
}
