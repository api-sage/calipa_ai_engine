import { CircleAlert, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { BrokenArt, type Art } from "./illustrations";

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  eyebrow?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 space-y-1.5">
        {eyebrow && <div className="text-small text-ink-muted">{eyebrow}</div>}
        <h1 className="font-serif text-display font-medium text-balance">{title}</h1>
        {description && <p className="max-w-2xl text-body text-ink-muted text-pretty">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}

/** Nothing to show. Pass `art` for a full illustration, or `icon` for a compact one. */
export function EmptyState({
  art: Illustration,
  icon: Icon,
  title,
  body,
  action,
  className,
}: {
  art?: Art;
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  body: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-4 px-6 py-14 text-center", className)}>
      {Illustration ? (
        <Illustration />
      ) : (
        Icon && (
          <span className="grid size-11 place-items-center rounded-xl bg-surface-2 text-ink-muted">
            <Icon className="size-5" />
          </span>
        )
      )}
      <div className="max-w-sm space-y-1">
        <div className="font-serif text-title font-medium">{title}</div>
        <p className="text-small text-ink-muted text-pretty">{body}</p>
      </div>
      {action}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  className,
  compact,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
  compact?: boolean;
}) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div className={cn("flex flex-col items-center justify-center gap-4 px-6 py-14 text-center", className)} role="alert">
      {compact ? (
        <span className="grid size-11 place-items-center rounded-xl bg-down-soft text-down">
          <CircleAlert className="size-5" />
        </span>
      ) : (
        <BrokenArt />
      )}
      <div className="max-w-sm space-y-1">
        <div className="font-serif text-title font-medium">{e?.title ?? "Something went wrong"}</div>
        <p className="text-small text-ink-muted text-pretty">
          {e?.message ?? (error instanceof Error ? error.message : "Try again in a moment.")}
        </p>
        {e && e.status > 0 && <p className="font-mono text-caption text-ink-subtle">HTTP {e.status}</p>}
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCcw /> Try again
        </Button>
      )}
    </div>
  );
}
