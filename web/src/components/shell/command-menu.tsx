"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useQueryClient } from "@tanstack/react-query";
import { Command as CommandPrimitive } from "cmdk";
import { CornerDownLeft, Inbox, Keyboard, Link2, Monitor, Moon, Palette, Plus, Scale, ScrollText, Search, Sun, UserRound } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { RunStatePill } from "@/components/calipa/run-state-pill";
import { formatRelative } from "@/components/calipa/timestamp";
import type { RunListItem } from "@/lib/api/client";
import { useMe, useReference, useRuns } from "@/lib/api/hooks";
import type { RunState } from "@/lib/run-state";
import { RUN_STATE_META } from "@/lib/run-state";
import { DEV_USERS, writeUserCookie } from "@/lib/users";
import { cn } from "@/lib/utils";

const item =
  "group relative flex cursor-default items-center gap-3 rounded-lg px-2.5 py-2 text-small outline-none select-none data-[selected=true]:bg-surface-2 [&_svg]:shrink-0";
const groupCls =
  "px-2 pb-1 [&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:text-caption [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-ink-subtle";

function Glyph({ icon: Icon, tone }: { icon: React.ComponentType<{ className?: string }>; tone?: string }) {
  return (
    <span className={cn("grid size-7 place-items-center rounded-md bg-surface-2 text-ink-muted group-data-[selected=true]:bg-surface", tone)}>
      <Icon className="size-4" />
    </span>
  );
}

export function CommandMenu({ open, onOpenChange, onShortcuts }: { open: boolean; onOpenChange: (o: boolean) => void; onShortcuts: () => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { setTheme } = useTheme();
  const [query, setQuery] = useState("");
  const me = useMe();
  const mine = useRuns("mine");
  const queue = useRuns("review_queue");
  const reference = useReference();

  // The person's own audits, then anything waiting for their review; each once.
  const runs = useMemo(() => {
    const seen = new Set<string>();
    const out: (RunListItem & { queued: boolean })[] = [];
    for (const [list, queued] of [[queue.data, true], [mine.data, false]] as const)
      for (const r of list ?? []) {
        if (seen.has(r.run_id)) continue;
        seen.add(r.run_id);
        out.push({ ...r, queued });
      }
    return out;
  }, [mine.data, queue.data]);

  function run(fn: () => void) {
    onOpenChange(false);
    setQuery("");
    fn();
  }

  const go = (href: string) => run(() => router.push(href));
  const searching = query.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={(o) => (onOpenChange(o), o || setQuery(""))}>
      <DialogContent showCloseButton={false} className="top-[14vh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-[620px]">
        <DialogTitle className="sr-only">Search Calipa</DialogTitle>
        <DialogDescription className="sr-only">Find an audit, a control or a page, or run an action.</DialogDescription>
        <CommandPrimitive loop className="flex min-w-0 flex-col" filter={(value, search, keywords) => {
          const hay = `${value} ${keywords?.join(" ") ?? ""}`.toLowerCase();
          return search.toLowerCase().split(/\s+/).every((w) => hay.includes(w)) ? 1 : 0;
        }}>
          <div className="flex items-center gap-3 border-b border-line px-4">
            <Search className="size-[18px] text-ink-subtle" />
            <CommandPrimitive.Input
              value={query}
              onValueChange={setQuery}
              placeholder="Search audits by request, id or person; controls; pages…"
              className="h-14 flex-1 bg-transparent text-body outline-none placeholder:text-ink-subtle"
            />
            <Kbd>esc</Kbd>
          </div>

          <CommandPrimitive.List className="max-h-[min(60vh,460px)] scroll-py-2 overflow-y-auto overscroll-contain pb-2">
            <CommandPrimitive.Empty className="flex flex-col items-center gap-1 py-12 text-center">
              <span className="font-serif text-title">Nothing matches “{query}”</span>
              <span className="text-small text-ink-muted">Try part of the request, the first characters of an id, or a control like C-FX.</span>
            </CommandPrimitive.Empty>

            {!searching && (
              <CommandPrimitive.Group heading="Go to" className={groupCls}>
                <CommandPrimitive.Item value="Audits" onSelect={() => go("/runs")} className={item}>
                  <Glyph icon={ScrollText} /> Audits <Shortcut keys={["G", "A"]} />
                </CommandPrimitive.Item>
                {me.data?.can_review && (
                  <CommandPrimitive.Item value="Review queue" onSelect={() => go("/review")} className={item}>
                    <Glyph icon={Inbox} /> Review queue
                    {!!queue.data?.length && <span className="figures font-mono text-caption text-signal">{queue.data.length}</span>}
                    <Shortcut keys={["G", "R"]} />
                  </CommandPrimitive.Item>
                )}
                <CommandPrimitive.Item value="Controls" onSelect={() => go("/controls")} className={item}>
                  <Glyph icon={Scale} /> Controls and access <Shortcut keys={["G", "C"]} />
                </CommandPrimitive.Item>
              </CommandPrimitive.Group>
            )}

            {runs.length > 0 && (
              <CommandPrimitive.Group heading={searching ? "Audits" : "Recent audits"} className={groupCls}>
                {(searching ? runs : runs.slice(0, 5)).map((r) => (
                  <CommandPrimitive.Item
                    key={r.run_id}
                    value={`audit ${r.run_id}`}
                    keywords={[r.request_text, r.initiated_by, r.state ? RUN_STATE_META[r.state as RunState].label : ""]}
                    onSelect={() => go(`/runs/${r.run_id}`)}
                    className={item}
                  >
                    <Glyph icon={r.queued ? Inbox : ScrollText} tone={r.queued ? "text-signal" : undefined} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-ink">{r.request_text}</span>
                      <span className="block truncate font-mono text-[11px] text-ink-subtle">
                        {r.run_id.slice(0, 8)} · {r.initiated_by} · {formatRelative(r.created_at)}
                      </span>
                    </span>
                    {r.state && <RunStatePill state={r.state as RunState} className="h-5 text-[11px]" />}
                  </CommandPrimitive.Item>
                ))}
              </CommandPrimitive.Group>
            )}

            {searching && reference.data && (
              <CommandPrimitive.Group heading="Controls" className={groupCls}>
                {reference.data.controls.map((c) => (
                  <CommandPrimitive.Item
                    key={c.control_id}
                    value={`control ${c.control_id}`}
                    keywords={[c.name, ...c.exception_types, ...c.objectives]}
                    onSelect={() => go(`/controls#${c.control_id}`)}
                    className={item}
                  >
                    <Glyph icon={Scale} />
                    <span className="min-w-0 flex-1 truncate">{c.name}</span>
                    <span className="font-mono text-caption text-ink-subtle">{c.control_id}</span>
                  </CommandPrimitive.Item>
                ))}
              </CommandPrimitive.Group>
            )}

            <CommandPrimitive.Group heading="Actions" className={groupCls}>
              {me.data?.can_initiate && (
                <CommandPrimitive.Item value="New audit start" onSelect={() => go("/runs/new")} className={item}>
                  <Glyph icon={Plus} tone="bg-brand-soft text-brand" /> New audit <Shortcut keys={["N"]} />
                </CommandPrimitive.Item>
              )}
              <CommandPrimitive.Item value="Copy link to this page" onSelect={() => run(() => { navigator.clipboard.writeText(location.href); toast.success("Link copied"); })} className={item}>
                <Glyph icon={Link2} /> Copy link to this page
              </CommandPrimitive.Item>
              {([["light", Sun], ["dark", Moon], ["system", Monitor]] as const).map(([t, Icon]) => (
                <CommandPrimitive.Item key={t} value={`Theme ${t}`} keywords={["appearance", "mode"]} onSelect={() => run(() => setTheme(t))} className={item}>
                  <Glyph icon={Icon} /> Use {t} theme
                </CommandPrimitive.Item>
              ))}
              <CommandPrimitive.Item value="Keyboard shortcuts help" onSelect={() => run(onShortcuts)} className={item}>
                <Glyph icon={Keyboard} /> Keyboard shortcuts <Shortcut keys={["?"]} />
              </CommandPrimitive.Item>
              <CommandPrimitive.Item value="Foundations design tokens" onSelect={() => go("/foundations")} className={item}>
                <Glyph icon={Palette} /> Foundations
              </CommandPrimitive.Item>
            </CommandPrimitive.Group>

            {searching && (
              <CommandPrimitive.Group heading="Act as (development)" className={groupCls}>
                {DEV_USERS.map((u) => (
                  <CommandPrimitive.Item
                    key={u.id}
                    value={`act as ${u.id}`}
                    keywords={[u.name, u.role, "switch user"]}
                    onSelect={() => run(() => { writeUserCookie(u.id); qc.clear(); qc.invalidateQueries(); toast(`Acting as ${u.name}`); })}
                    className={item}
                  >
                    <Glyph icon={UserRound} /> Act as {u.name}
                    <span className="ml-auto font-mono text-caption text-ink-subtle">{u.id}</span>
                  </CommandPrimitive.Item>
                ))}
              </CommandPrimitive.Group>
            )}
          </CommandPrimitive.List>

          <div className="flex items-center gap-4 border-t border-line bg-canvas/60 px-4 py-2 text-caption text-ink-subtle">
            <span className="flex items-center gap-1.5"><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span>
            <span className="flex items-center gap-1.5"><Kbd><CornerDownLeft className="size-3" /></Kbd> open</span>
            <span className="ml-auto">{runs.length > 0 && `${runs.length} audits searchable`}</span>
          </div>
        </CommandPrimitive>
      </DialogContent>
    </Dialog>
  );
}

function Shortcut({ keys }: { keys: string[] }) {
  return (
    <span className="ml-auto flex gap-1 opacity-0 transition-opacity group-data-[selected=true]:opacity-100">
      {keys.map((k) => (
        <Kbd key={k}>{k}</Kbd>
      ))}
    </span>
  );
}
