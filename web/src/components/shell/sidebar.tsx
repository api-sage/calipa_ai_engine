"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { CircleHelp, Inbox, Palette, Plus, Scale, ScrollText } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useMe, useRuns } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";
import { LogoMark, Wordmark } from "./logo";
import { useShell } from "./shell-context";

type Icon = React.ComponentType<{ className?: string; strokeWidth?: number }>;

function Hint({ show, label, children }: { show: boolean; label: React.ReactNode; children: React.ReactNode }) {
  if (!show) return <>{children}</>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right" sideOffset={10}>
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

function Label({ open, children }: { open: boolean; children: React.ReactNode }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.span
          initial={{ opacity: 0, x: -4 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -4, transition: { duration: 0.08 } }}
          transition={{ duration: 0.18, delay: 0.05 }}
          className="relative flex-1 truncate"
        >
          {children}
        </motion.span>
      )}
    </AnimatePresence>
  );
}

function NavItem({
  href,
  icon: Icon,
  label,
  count,
  active,
  open,
}: {
  href: string;
  icon: Icon;
  label: string;
  count?: number;
  active: boolean;
  open: boolean;
}) {
  const badge = count !== undefined && count > 0;
  return (
    <Hint show={!open} label={badge ? `${label} · ${count}` : label}>
      <Link
        href={href}
        aria-current={active ? "page" : undefined}
        aria-label={open ? undefined : label}
        className={cn(
          "relative flex h-9 items-center gap-3 rounded-lg px-[11px] text-body outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50",
          active ? "text-ink" : "text-ink-muted hover:bg-surface-3/70 hover:text-ink",
        )}
      >
        {active && (
          <motion.span
            layoutId="nav-active"
            className="absolute inset-0 rounded-lg bg-surface shadow-raised"
            transition={{ type: "spring", duration: 0.35, bounce: 0.1 }}
          />
        )}
        <span className="relative">
          <Icon className={cn("size-[18px]", active && "text-brand")} strokeWidth={1.75} />
          {badge && !open && <span className="absolute -top-1 -right-1 size-2 rounded-full bg-signal ring-2 ring-canvas" />}
        </span>
        <Label open={open}>{label}</Label>
        {badge && open && (
          <span className="figures relative grid h-5 min-w-5 place-items-center rounded-full bg-signal-soft px-1.5 font-mono text-[11px] font-medium text-signal">
            {count}
          </span>
        )}
      </Link>
    </Hint>
  );
}

export function Sidebar({ open }: { open: boolean }) {
  const path = usePathname();
  const me = useMe();
  const canReview = me.data?.can_review ?? false;
  const queue = useRuns("review_queue");
  const { openShortcuts } = useShell();

  return (
    <motion.aside
      initial={false}
      animate={{ width: open ? 232 : 60 }}
      transition={{ type: "spring", duration: 0.4, bounce: 0 }}
      className="flex h-full shrink-0 flex-col gap-5 overflow-hidden px-2.5 py-3"
    >
      <Link
        href="/runs"
        aria-label="Calipa home"
        className="flex h-10 items-center gap-2.5 rounded-lg px-[3px] outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <LogoMark className="size-[34px] shrink-0" />
        <AnimatePresence initial={false}>
          {open && (
            <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.08 } }} className="flex items-center gap-2">
              <Wordmark className="h-[21px]" />
              <span className="rounded bg-surface-3 px-1 py-px font-mono text-[9.5px] font-medium tracking-wider text-ink-subtle uppercase">Dev</span>
            </motion.span>
          )}
        </AnimatePresence>
      </Link>

      {/* Hold the button's place while /me loads, so the nav below does not jump. */}
      {me.isPending && <span className="h-9 shrink-0 rounded-lg bg-surface-3/60" />}
      {(me.data?.can_initiate ?? false) && (
        <Hint show={!open} label="New audit">
          <Link
            href="/runs/new"
            aria-label={open ? undefined : "New audit"}
            className={cn(
              "flex h-9 items-center gap-3 rounded-lg bg-brand px-[11px] text-body font-medium text-[#faf9f5] shadow-[inset_0_1px_0_rgb(255_255_255/0.12)] outline-none transition-[background-color,transform] hover:bg-brand/90 focus-visible:ring-2 focus-visible:ring-ring/50 active:scale-[0.98] dark:text-[#111110]",
              path === "/runs/new" && "ring-2 ring-brand/25",
            )}
          >
            <Plus className="size-[18px] shrink-0" strokeWidth={2} />
            <Label open={open}>New audit</Label>
          </Link>
        </Hint>
      )}

      <nav className="flex flex-col gap-0.5" aria-label="Main">
        <NavItem open={open} href="/runs" icon={ScrollText} label="Audits" active={path === "/runs" || (path.startsWith("/runs/") && path !== "/runs/new")} />
        {canReview && (
          <NavItem open={open} href="/review" icon={Inbox} label="Review queue" count={queue.data?.length} active={path === "/review"} />
        )}
        <NavItem open={open} href="/controls" icon={Scale} label="Controls" active={path === "/controls"} />
      </nav>

      <div className="mt-auto flex flex-col gap-0.5">
        <NavItem open={open} href="/foundations" icon={Palette} label="Foundations" active={path === "/foundations"} />
        <Hint show={!open} label="Keyboard shortcuts">
          <button
            onClick={openShortcuts}
            aria-label={open ? undefined : "Keyboard shortcuts"}
            className="flex h-9 items-center gap-3 rounded-lg px-[11px] text-left text-body text-ink-muted outline-none transition-colors hover:bg-surface-3/70 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <CircleHelp className="size-[18px] shrink-0" strokeWidth={1.75} />
            <Label open={open}>Shortcuts</Label>
          </button>
        </Hint>
      </div>
    </motion.aside>
  );
}
