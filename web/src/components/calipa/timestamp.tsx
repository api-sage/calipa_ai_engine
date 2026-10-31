"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const BANK_TZ = "Africa/Lagos";

function relative(from: Date, now: Date) {
  const s = Math.round((now.getTime() - from.getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s ago`.replace(/ 0s/, "");
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m ago`.replace(/ 0m/, "");
  const d = Math.floor(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

function absolute(date: Date) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: BANK_TZ,
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

/** Relative time that keeps ticking; the exact time in the bank's timezone on hover. */
export function Timestamp({ iso, className }: { iso: string; className?: string }) {
  const date = new Date(iso);
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <time dateTime={iso} className={cn("figures cursor-default text-ink-muted", className)}>
          {/* Server render shows the absolute time; the client swaps in the relative one. */}
          {now ? relative(date, now) : absolute(date)}
        </time>
      </TooltipTrigger>
      <TooltipContent className="figures">
        {absolute(date)} · {BANK_TZ}
      </TooltipContent>
    </Tooltip>
  );
}

export const formatRelative = (iso: string) => relative(new Date(iso), new Date());

const dayFmt = new Intl.DateTimeFormat("en-GB", { timeZone: BANK_TZ, day: "numeric", month: "short", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat("en-GB", { timeZone: BANK_TZ, hour: "2-digit", minute: "2-digit" });

/** A date for tables: the day, and the time under it, in the bank's timezone. */
export function DateCell({ iso, className }: { iso: string; className?: string }) {
  const d = new Date(iso);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <time dateTime={iso} className={cn("figures block cursor-default leading-tight", className)}>
          <span className="block text-small text-ink">{dayFmt.format(d)}</span>
          <span className="block font-mono text-[11px] text-ink-subtle">{timeFmt.format(d)}</span>
        </time>
      </TooltipTrigger>
      <TooltipContent className="figures">
        {absolute(d)} · {BANK_TZ}
      </TooltipContent>
    </Tooltip>
  );
}
