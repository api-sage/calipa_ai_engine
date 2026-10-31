"use client";

import { ThemeProvider as NextThemes, useTheme } from "next-themes";
import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemes attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {children}
    </NextThemes>
  );
}

const OPTIONS = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "system", icon: Monitor, label: "System" },
  { value: "dark", icon: Moon, label: "Dark" },
] as const;

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div role="radiogroup" aria-label="Theme" className="inline-flex h-8 items-center rounded-lg bg-surface-2 p-0.5">
      {OPTIONS.map(({ value, icon: Icon, label }) => {
        const active = mounted && theme === value;
        return (
          <button
            key={value}
            role="radio"
            aria-checked={active}
            aria-label={label}
            onClick={() => setTheme(value)}
            className={cn(
              "inline-flex h-7 w-8 items-center justify-center rounded-md text-ink-muted transition-all outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
              active ? "bg-surface text-ink shadow-raised" : "hover:text-ink",
            )}
          >
            <Icon className="size-4" />
          </button>
        );
      })}
    </div>
  );
}

const NEXT: Record<string, (typeof OPTIONS)[number]["value"]> = { light: "dark", dark: "system", system: "light" };

/** One button for the top bar: light → dark → system. The icon shows the current choice. */
export function ThemeButton({ className }: { className?: string }) {
  const { theme = "system", setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  // The stored theme is only known on the client; render neutral labels until then.
  const current = (mounted && OPTIONS.find((o) => o.value === theme)) || OPTIONS[1];
  const next = OPTIONS.find((o) => o.value === NEXT[current.value])!;
  return (
    <button
      onClick={() => setTheme(next.value)}
      aria-label={mounted ? `Theme: ${current.label}. Switch to ${next.label.toLowerCase()}` : "Theme"}
      title={mounted ? `Theme: ${current.label}` : "Theme"}
      className={cn(
        "grid size-8 place-items-center rounded-lg text-ink-muted outline-none transition-colors hover:bg-surface-2 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/50",
        className,
      )}
    >
      {mounted && <current.icon className="size-[18px]" strokeWidth={1.75} />}
    </button>
  );
}
