"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Keyboard } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DEV_USERS, readUserCookie, writeUserCookie } from "@/lib/users";
import { cn } from "@/lib/utils";
import { useShell } from "./shell-context";

export function initials(id: string) {
  const name = DEV_USERS.find((u) => u.id === id)?.name ?? id;
  return name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/** A person's initials in a circle. Each dev user keeps one tint, so faces are learnable. */
export function Avatar({ id, className }: { id: string; className?: string }) {
  const i = DEV_USERS.findIndex((u) => u.id === id);
  const tint = ["bg-brand-soft text-brand-ink", "bg-ai-soft text-ai", "bg-attention-soft text-attention", "bg-verified-soft text-verified"][
    i < 0 ? 0 : i % 4
  ];
  return (
    <span
      className={cn("grid size-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold", tint, className)}
      title={id}
      aria-hidden
    >
      {initials(id)}
    </span>
  );
}

/** Development sign-in: act as one of the entitlement file's users. Goes when SSO lands. */
export function UserMenu() {
  const qc = useQueryClient();
  const { openShortcuts } = useShell();
  const [user, setUser] = useState<string | null>(null);
  useEffect(() => setUser(readUserCookie()), []);
  const current = DEV_USERS.find((u) => u.id === user);

  function choose(id: string) {
    writeUserCookie(id);
    setUser(id);
    qc.clear(); // everything cached was seen as someone else
    qc.invalidateQueries();
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="flex items-center gap-2 rounded-full p-0.5 pr-0.5 outline-none transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring/50 data-[state=open]:bg-surface-2"
        aria-label={current ? `Signed in as ${current.name}` : "Account"}
      >
        {user ? <Avatar id={user} className="size-8" /> : <span className="size-8 rounded-full bg-surface-3" />}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="w-64">
        {current && (
          <>
            <div className="flex items-center gap-2.5 px-2 py-2">
              <Avatar id={current.id} className="size-9 text-caption" />
              <div className="min-w-0">
                <div className="truncate text-small font-medium">{current.name}</div>
                <div className="truncate font-mono text-[11px] text-ink-subtle">{current.id}</div>
              </div>
            </div>
            <DropdownMenuSeparator />
          </>
        )}
        <DropdownMenuLabel className="text-caption font-normal text-ink-subtle">Act as (development only)</DropdownMenuLabel>
        {DEV_USERS.map((u) => (
          <DropdownMenuItem key={u.id} onSelect={() => choose(u.id)} className="gap-2.5 py-1.5">
            <Avatar id={u.id} />
            <span className="flex-1">
              <span className="block text-small">{u.name}</span>
              <span className="block text-caption text-ink-subtle">{u.role}</span>
            </span>
            <Check className={cn("size-4 text-brand", u.id === user ? "opacity-100" : "opacity-0")} />
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={openShortcuts} className="gap-2.5">
          <Keyboard className="size-4" /> Keyboard shortcuts
          <span className="ml-auto font-mono text-caption text-ink-subtle">?</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
