"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ShortcutsDialog } from "@/components/calipa/dialogs";
import { useMe } from "@/lib/api/hooks";
import { CommandMenu } from "./command-menu";
import { ShellContext } from "./shell-context";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

const SIDEBAR_KEY = "calipa.sidebar";

function typing(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  return !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
}

/**
 * The frame every signed-in page sits in: an icon rail on the canvas, and one
 * raised sheet with a top bar (breadcrumbs, search, theme, account).
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const me = useMe();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [search, setSearch] = useState(false);
  const [shortcuts, setShortcuts] = useState(false);
  const [title, setTitle] = useState<string | null>(null);
  const pendingG = useRef<number | null>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_KEY);
      if (saved !== null) setSidebarOpen(saved === "open");
    } catch {}
  }, []);

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((o) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, o ? "closed" : "open");
      } catch {}
      return !o;
    });
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearch((s) => !s);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e) || document.querySelector("[role=dialog]")) return;
      if (pendingG.current !== null) {
        window.clearTimeout(pendingG.current);
        pendingG.current = null;
        const to = { a: "/runs", r: "/review", c: "/controls" }[e.key.toLowerCase()];
        if (to) {
          e.preventDefault();
          router.push(to);
        }
        return;
      }
      switch (e.key) {
        case "/":
          e.preventDefault();
          setSearch(true);
          break;
        case "?":
          e.preventDefault();
          setShortcuts(true);
          break;
        case "[":
          toggleSidebar();
          break;
        case "n":
        case "N":
          if (me.data?.can_initiate) router.push("/runs/new");
          break;
        case "g":
        case "G":
          pendingG.current = window.setTimeout(() => (pendingG.current = null), 900);
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, toggleSidebar, me.data?.can_initiate]);

  const shell = useMemo(
    () => ({ openSearch: () => setSearch(true), openShortcuts: () => setShortcuts(true), setTitle }),
    [],
  );

  return (
    <ShellContext.Provider value={shell}>
      <div className="flex h-dvh overflow-hidden bg-canvas">
        <Sidebar open={sidebarOpen} />
        <div className="min-w-0 flex-1 py-2 pr-2">
          <div className="flex h-full flex-col overflow-hidden rounded-2xl bg-surface shadow-raised">
            <Topbar title={title} onToggleSidebar={toggleSidebar} />
            <main id="main" className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
              {children}
            </main>
          </div>
        </div>
      </div>
      <CommandMenu open={search} onOpenChange={setSearch} onShortcuts={() => setShortcuts(true)} />
      <ShortcutsDialog open={shortcuts} onOpenChange={setShortcuts} />
    </ShellContext.Provider>
  );
}
