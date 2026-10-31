"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { PanelLeft, Search } from "lucide-react";
import { ThemeButton } from "@/components/calipa/theme";
import { Kbd } from "@/components/ui/kbd";
import { crumbsFor, useShell } from "./shell-context";
import { UserMenu } from "./user-switch";

export function Topbar({ title, onToggleSidebar }: { title: string | null; onToggleSidebar: () => void }) {
  const path = usePathname();
  const { openSearch } = useShell();
  const crumbs = crumbsFor(path, title);

  return (
    <header className="sticky top-0 z-20 grid h-14 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-4 border-b border-line bg-surface/85 px-3 backdrop-blur-md">
      <div className="flex min-w-0 items-center gap-1">
        <button
          onClick={onToggleSidebar}
          aria-label="Toggle sidebar"
          title="Toggle sidebar  ["
          className="grid size-8 shrink-0 place-items-center rounded-lg text-ink-muted outline-none transition-colors hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <PanelLeft className="size-[18px]" strokeWidth={1.75} />
        </button>
        {crumbs.length > 0 && <span className="mx-1 h-4 w-px bg-line" />}
        <nav aria-label="Breadcrumb" className="min-w-0">
          <ol className="flex min-w-0 items-center gap-1.5 text-small">
            <AnimatePresence initial={false} mode="popLayout">
              {crumbs.map((c, i) => (
                <motion.li
                  key={`${i}-${c.label}`}
                  initial={{ opacity: 0, y: 3 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.18 }}
                  className="flex min-w-0 items-center gap-1.5"
                >
                  {i > 0 && <span className="text-line-strong">/</span>}
                  {c.href ? (
                    <Link href={c.href} className="shrink-0 rounded px-1 text-ink-muted outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50">
                      {c.label}
                    </Link>
                  ) : (
                    <span className="truncate px-1 text-ink" aria-current="page">
                      {c.label}
                    </span>
                  )}
                </motion.li>
              ))}
            </AnimatePresence>
          </ol>
        </nav>
      </div>

      <button
        onClick={openSearch}
        className="group flex h-9 w-[min(42vw,440px)] items-center gap-2.5 rounded-lg border border-line bg-canvas/70 px-3 text-small text-ink-subtle outline-none transition-colors hover:border-line-strong hover:bg-canvas focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <Search className="size-4 text-ink-muted" />
        <span className="flex-1 truncate text-left">Search audits, controls, pages…</span>
        <Kbd className="font-mono">⌘K</Kbd>
      </button>

      <div className="flex items-center justify-end gap-1">
        <ThemeButton />
        <span className="mx-1 h-4 w-px bg-line" />
        <UserMenu />
      </div>
    </header>
  );
}
