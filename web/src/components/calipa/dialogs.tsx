"use client";

import { useState } from "react";
import { Check, Copy, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { BrokenArt, type Art } from "./illustrations";

type Tone = "brand" | "attention" | "down";

const TONE_BUTTON: Record<Tone, "brand" | "default" | "destructive"> = {
  brand: "brand",
  attention: "default",
  down: "destructive",
};

/**
 * Asks before something that cannot be taken back: a signed review, a run that
 * spends money. The body says exactly what will happen; the button repeats the verb.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  confirm,
  tone = "brand",
  pending,
  onConfirm,
  icon: Icon,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  children?: React.ReactNode;
  confirm: string;
  tone?: Tone;
  pending?: boolean;
  onConfirm: () => void;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !pending && onOpenChange(o)}>
      <DialogContent className="gap-5 sm:max-w-md" showCloseButton={!pending}>
        <DialogHeader className="gap-3 text-left">
          {Icon && (
            <span
              className={cn(
                "grid size-10 place-items-center rounded-xl",
                tone === "brand" && "bg-brand-soft text-brand",
                tone === "attention" && "bg-attention-soft text-attention",
                tone === "down" && "bg-down-soft text-down",
              )}
            >
              <Icon className="size-5" />
            </span>
          )}
          <DialogTitle className="font-serif text-title font-medium">{title}</DialogTitle>
          <DialogDescription className="text-small text-ink-muted text-pretty">{description}</DialogDescription>
        </DialogHeader>
        {children}
        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button variant={TONE_BUTTON[tone]} onClick={onConfirm} loading={pending} autoFocus>
            {confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * A failure the person must read before going on, such as a conflict or a server
 * error on a decision. Shows what Calipa said and lets them copy it for support.
 * Minor failures use a toast instead.
 */
export function ErrorDialog({
  error,
  onOpenChange,
  onRetry,
  retryLabel = "Try again",
  art: Illustration = BrokenArt,
}: {
  error: unknown;
  onOpenChange: (open: boolean) => void;
  onRetry?: () => void;
  retryLabel?: string;
  art?: Art;
}) {
  const [copied, setCopied] = useState(false);
  const e = error instanceof ApiError ? error : null;
  const message = e?.message ?? (error instanceof Error ? error.message : "Something went wrong.");
  const title = e?.title ?? "Something went wrong";

  function copy() {
    navigator.clipboard.writeText(`${title}\n${e ? `HTTP ${e.status}\n` : ""}${message}\nat ${new Date().toISOString()} on ${location.pathname}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }

  return (
    <Dialog open={!!error} onOpenChange={onOpenChange}>
      <DialogContent className="gap-5 sm:max-w-md">
        <div className="flex justify-center pt-2">
          <Illustration />
        </div>
        <DialogHeader className="items-center text-center">
          <DialogTitle className="font-serif text-title font-medium">{title}</DialogTitle>
          <DialogDescription className="text-small text-ink-muted text-pretty">{message}</DialogDescription>
        </DialogHeader>
        {e && e.status > 0 && (
          <div className="flex items-center justify-between rounded-lg bg-surface-2 px-3 py-2 font-mono text-caption text-ink-muted">
            <span>HTTP {e.status}</span>
            <button onClick={copy} className="inline-flex items-center gap-1 rounded px-1 hover:text-ink">
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copied ? "Copied" : "Copy details"}
            </button>
          </div>
        )}
        <DialogFooter className="gap-2 sm:justify-center sm:gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          {onRetry && (
            <Button
              onClick={() => {
                onOpenChange(false);
                onRetry();
              }}
            >
              <RotateCcw /> {retryLabel}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["⌘", "K"], label: "Search audits, controls and pages" },
  { keys: ["/"], label: "Search" },
  { keys: ["N"], label: "New audit" },
  { keys: ["G", "A"], label: "Go to audits" },
  { keys: ["G", "R"], label: "Go to the review queue" },
  { keys: ["G", "C"], label: "Go to controls" },
  { keys: ["["], label: "Collapse or expand the sidebar" },
  { keys: ["⌘", "↵"], label: "Submit a form" },
  { keys: ["?"], label: "Show this list" },
];

export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-4 sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="font-serif text-title font-medium">Keyboard shortcuts</DialogTitle>
          <DialogDescription className="text-small text-ink-muted">Anywhere outside a text field.</DialogDescription>
        </DialogHeader>
        <ul className="divide-y divide-line">
          {SHORTCUTS.map((s) => (
            <li key={s.label} className="flex items-center justify-between gap-4 py-2 text-small">
              <span className="text-ink-muted">{s.label}</span>
              <span className="flex gap-1">
                {s.keys.map((k) => (
                  <Kbd key={k}>{k}</Kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
