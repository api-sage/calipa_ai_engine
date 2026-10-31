"use client";

import { createContext, useContext, useEffect } from "react";

export type Crumb = { label: string; href?: string };

type Shell = {
  openSearch: () => void;
  openShortcuts: () => void;
  /** The page's own last crumb (an audit's request, say), replacing the id in the path. */
  setTitle: (title: string | null) => void;
};

export const ShellContext = createContext<Shell>({
  openSearch: () => {},
  openShortcuts: () => {},
  setTitle: () => {},
});

export const useShell = () => useContext(ShellContext);

/** Names the current page in the breadcrumbs for as long as it is mounted. */
export function usePageTitle(title: string | null | undefined) {
  const { setTitle } = useShell();
  useEffect(() => {
    setTitle(title ?? null);
    return () => setTitle(null);
  }, [title, setTitle]);
}

const SECTIONS: Record<string, string> = {
  runs: "Audits",
  review: "Review queue",
  controls: "Controls",
  foundations: "Foundations",
};

export function crumbsFor(path: string, title: string | null): Crumb[] {
  const [section, sub] = path.split("/").filter(Boolean);
  const head = SECTIONS[section];
  if (!head) return title ? [{ label: title }] : [];
  if (!sub) return [{ label: head }];
  const leaf = sub === "new" ? "New audit" : (title ?? sub.slice(0, 8));
  return [{ label: head, href: `/${section}` }, { label: leaf }];
}
