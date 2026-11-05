"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Download, FileUp, Play, ShieldCheck, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ConfirmDialog, ErrorDialog } from "@/components/calipa/dialogs";
import { ACCOUNT_ROLES, OBJECTIVES, objectiveLabel } from "@/lib/labels";
import { api, type AuditObjective, type UploadPreview, type UploadRejected } from "@/lib/api/client";
import { useCreateRun, useMe, useUploadWorkbook } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

function newKey() {
  return `ui-${crypto.randomUUID()}`;
}

type Rejected = { message: string; issues: UploadRejected["issues"]; total: number };

function asRejected(status: number, message: string, body: unknown): Rejected {
  if (body && typeof body === "object" && "issues" in body) {
    const r = body as Partial<UploadRejected>;
    return {
      message: typeof r.detail === "string" ? r.detail : message,
      issues: Array.isArray(r.issues) ? r.issues : [],
      total: typeof r.total_issues === "number" ? r.total_issues : 0,
    };
  }
  return { message: status === 0 ? message : `${message} (${status})`, issues: [], total: 0 };
}

/** Start an audit from an uploaded workbook: drop the file, check the preview, pick the scope. */
export function UploadAudit() {
  const me = useMe();
  const router = useRouter();
  const upload = useUploadWorkbook();
  const create = useCreateRun();
  const [key, setKey] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [rejected, setRejected] = useState<Rejected | null>(null);
  const [objective, setObjective] = useState<AuditObjective | "">("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => setKey(newKey()), []);
  useEffect(() => {
    if (!objective && me.data && me.data.objectives.length > 0) {
      const objectives = me.data.objectives as AuditObjective[];
      setObjective(
        objectives.includes("statement_reconciliation") ? "statement_reconciliation" : objectives[0],
      );
    }
  }, [objective, me.data]);

  function send(file: File) {
    setPreview(null);
    setRejected(null);
    upload.mutate(file, {
      onSuccess: (p) => {
        setPreview(p);
        setStart(p.period_start);
        setEnd(p.period_end);
      },
      onError: (e) => setRejected(asRejected(e.status, e.message, e.body)),
    });
  }

  function pick() {
    input.current?.click();
  }

  const ready =
    !!preview && !!objective && !!start && !!end && start <= end && !!key && !create.isPending;
  const summary =
    preview && objective && start && end
      ? `Reconcile ${preview.filename}: ${objectiveLabel(objective)} for ${start} to ${end}${note.trim() ? ` — ${note.trim().slice(0, 280)}` : ""}`
      : "";

  function startRun() {
    if (!ready || !preview || !key) return;
    create.mutate(
      {
        text: summary,
        key,
        upload: {
          upload_sha256: preview.upload_sha256,
          upload_filename: preview.filename,
          scope: {
            objective,
            period_start: start,
            period_end: end,
            currencies: [],
            transaction_references: [],
          },
        },
      },
      {
        onSuccess: (run) => {
          setConfirming(false);
          setKey(newKey());
          router.push(`/runs/${run.run_id}`);
        },
        onError: (e) => {
          setConfirming(false);
          setError(e);
        },
      },
    );
  }

  return (
    <div className="space-y-6">
      <input
        ref={input}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        aria-hidden
        tabIndex={-1}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) send(file);
        }}
      />
      {!preview && !rejected && (
        <div
          role="button"
          tabIndex={0}
          aria-label="Upload the GL workbook"
          onClick={pick}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") pick();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files?.[0];
            if (file && !upload.isPending) send(file);
          }}
          className={cn(
            "cursor-pointer rounded-2xl border border-dashed bg-surface px-6 py-10 text-center shadow-raised transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            dragging ? "border-brand/60 bg-brand-soft/30" : "border-line hover:border-line-strong",
          )}
        >
          <div className="mx-auto grid size-11 place-items-center rounded-xl bg-surface-2 text-ink-muted">
            <FileUp className="size-5" />
          </div>
          <p className="mt-4 text-lead font-medium">
            {upload.isPending ? "Checking the workbook…" : "Drop the GL workbook here"}
          </p>
          <p className="mt-1 text-small text-ink-muted">
            {upload.isPending
              ? "Every row is validated before anything starts."
              : "An .xlsx with GL Transactions and, optionally, a Statement sheet."}
          </p>
          <div className="mt-5 flex items-center justify-center gap-3">
            <Button variant="brand" disabled={upload.isPending} loading={upload.isPending}>
              Choose file
            </Button>
            <a
              href={api.templateUrl}
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1.5 text-small text-ink-muted underline-offset-4 hover:text-ink hover:underline"
            >
              <Download className="size-3.5" /> Template
            </a>
          </div>
        </div>
      )}

      {rejected && (
        <div className="rounded-2xl border border-down/40 bg-surface px-6 py-6 shadow-raised">
          <div className="flex items-center gap-2 text-body font-medium text-down">
            <TriangleAlert className="size-4" /> The file needs attention first
          </div>
          <p className="mt-1 text-small text-ink-muted">
            {rejected.total > 0
              ? `${rejected.total} row${rejected.total === 1 ? "" : "s"} to fix. Nothing was kept; fix the file and try again.`
              : rejected.message}
          </p>
          {rejected.issues.length > 0 && (
            <ul className="mt-4 max-h-64 space-y-1.5 overflow-auto rounded-lg bg-surface-2 px-4 py-3">
              {rejected.issues.slice(0, 12).map((issue, i) => (
                <li key={i} className="text-small text-ink">
                  <span className="figures font-mono text-caption text-ink-subtle">
                    {issue.sheet} · row {issue.row}
                    {issue.column ? ` · ${issue.column}` : ""} —{" "}
                  </span>
                  {issue.message}
                </li>
              ))}
              {rejected.total > rejected.issues.slice(0, 12).length && (
                <li className="text-small text-ink-subtle">
                  And {rejected.total - Math.min(rejected.total, 12)} more…
                </li>
              )}
            </ul>
          )}
          <div className="mt-5 flex items-center gap-3">
            <Button variant="brand" onClick={pick} disabled={upload.isPending}>
              Try another file
            </Button>
            <a
              href={api.templateUrl}
              className="inline-flex items-center gap-1.5 text-small text-ink-muted underline-offset-4 hover:text-ink hover:underline"
            >
              <Download className="size-3.5" /> Template
            </a>
          </div>
        </div>
      )}

      {preview && (
        <div className="rounded-2xl border border-line bg-surface px-6 py-6 shadow-raised">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-lead font-medium">{preview.filename}</h2>
            <button
              onClick={pick}
              className="text-small text-ink-muted underline-offset-4 hover:text-ink hover:underline"
            >
              Use a different file
            </button>
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-small sm:grid-cols-4">
            <div>
              <dt className="text-caption text-ink-subtle">GL entries</dt>
              <dd className="figures font-medium">{preview.gl_entries}</dd>
            </div>
            <div>
              <dt className="text-caption text-ink-subtle">Statement lines</dt>
              <dd className="figures font-medium">
                {preview.has_statement ? preview.statement_lines : "None"}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-ink-subtle">File covers</dt>
              <dd className="figures font-medium">
                {preview.period_start} → {preview.period_end}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-ink-subtle">Currencies</dt>
              <dd className="figures font-medium">{preview.currencies.join(", ") || "—"}</dd>
            </div>
          </dl>
          {!preview.has_statement && (
            <p className="mt-3 text-small text-ink-muted">
              No Statement sheet, so the run checks the ledger on its own without statement breaks.
            </p>
          )}
          {preview.warnings.length > 0 && (
            <ul className="mt-3 space-y-1">
              {preview.warnings.map((w, i) => (
                <li key={i} className="flex items-start gap-1.5 text-small text-ink-muted">
                  <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-attention" /> {w}
                </li>
              ))}
            </ul>
          )}

          <div className="mt-6 grid gap-4 border-t border-line pt-6 sm:grid-cols-3">
            <div className="space-y-2 sm:col-span-1">
              <Label htmlFor="objective">What to check</Label>
              <select
                id="objective"
                value={objective}
                onChange={(e) => setObjective(e.target.value as AuditObjective)}
                className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                {(me.data?.objectives ?? []).map((o) => (
                  <option key={o} value={o}>
                    {OBJECTIVES[o]?.label ?? objectiveLabel(o)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="period-start">From</Label>
              <Input
                id="period-start"
                type="date"
                value={start}
                max={end || undefined}
                onChange={(e) => setStart(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="period-end">To</Label>
              <Input
                id="period-end"
                type="date"
                value={end}
                min={start || undefined}
                onChange={(e) => setEnd(e.target.value)}
              />
            </div>
          </div>
          {objective && OBJECTIVES[objective] && (
            <p className="mt-2 text-small text-ink-muted">{OBJECTIVES[objective].what}</p>
          )}
          <div className="mt-4 space-y-2">
            <Label htmlFor="note">
              Note for the report <span className="text-ink-subtle">(optional)</span>
            </Label>
            <Textarea
              id="note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              maxLength={1000}
              placeholder="Anything the reviewer should know about this file"
            />
          </div>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
            <span className="flex items-center gap-1.5 text-caption text-ink-subtle">
              <ShieldCheck className="size-3.5 text-verified" />
              Read-only. Calipa cannot move money or change the ledger.
            </span>
            <Button
              variant="brand"
              onClick={() => ready && setConfirming(true)}
              disabled={!ready}
              loading={create.isPending}
            >
              Start audit <ArrowRight />
            </Button>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Start this audit?"
        description="Calipa reconciles the uploaded file and explains every break with evidence. Meta Muse Spark helps explain the evidence, and every step is recorded in the audit trail."
        confirm="Start audit"
        icon={Play}
        pending={create.isPending}
        onConfirm={startRun}
      >
        <div className="space-y-3">
          <blockquote className="rounded-lg bg-surface-2 px-4 py-3 font-serif text-lead text-ink text-pretty">
            “{summary}”
          </blockquote>
          {preview && (
            <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5 text-caption">
              <dt className="text-ink-subtle">File</dt>
              <dd className="text-ink">
                {preview.filename} ({preview.gl_entries} entries
                {preview.has_statement ? `, ${preview.statement_lines} statement lines` : ""})
              </dd>
              <dt className="text-ink-subtle">You may audit</dt>
              <dd className="text-ink">
                {(me.data?.account_roles ?? []).map((r) => ACCOUNT_ROLES[r] ?? r).join(", ")}
              </dd>
            </dl>
          )}
        </div>
      </ConfirmDialog>
      <ErrorDialog error={error} onOpenChange={(o) => !o && setError(null)} onRetry={startRun} retryLabel="Try again" />
    </div>
  );
}
