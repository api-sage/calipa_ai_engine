"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type EventView, type ReviewDecision, type RunView } from "./client";
import { RUN_STATE_META, type RunState } from "@/lib/run-state";

export const keys = {
  me: ["me"] as const,
  reference: ["reference"] as const,
  runs: (view: string) => ["runs", view] as const,
  run: (id: string) => ["run", id] as const,
  events: (id: string) => ["events", id] as const,
  report: (id: string) => ["report", id] as const,
};

const isWorking = (state: string | null | undefined) =>
  !!state && RUN_STATE_META[state as RunState]?.family === "working";

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: api.me, staleTime: 60_000 });
}

/** The control catalogue, accounts and tariff. Versioned files; they change with a deploy. */
export function useReference() {
  return useQuery({ queryKey: keys.reference, queryFn: api.reference, staleTime: Infinity });
}

export function useRuns(view: "mine" | "review_queue") {
  return useQuery({
    queryKey: keys.runs(view),
    queryFn: () => api.runs(view),
    // Lists refresh quietly; a run's own page streams.
    refetchInterval: 10_000,
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: keys.run(id),
    queryFn: () => api.run(id),
    refetchInterval: (q) => (isWorking(q.state.data?.state) ? 5_000 : false),
  });
}

export function useReport(id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.report(id), queryFn: () => api.report(id), enabled, staleTime: Infinity });
}

export function useEvents(id: string) {
  return useQuery({ queryKey: keys.events(id), queryFn: () => api.events(id) });
}

export type StreamStatus = "live" | "reconnecting" | "offline";

// Every event type the backend records (grep event_type in src/calipa).
const EVENT_TYPES = [
  "transition",
  "worker_claimed",
  "interpretation_attempt",
  "clarification_answered",
  "extraction_checkpoint",
  "analysis_attempt",
  "finding_recorded",
  "report_attempt",
  "report_rendered",
  "step_failed",
  "reinvestigation_of",
];

/**
 * The run's event stream. New events are appended to the events query and the run
 * is refetched on each transition, so every view of the run stays current. The API
 * closes the stream when the run finishes or waits for a person; that is "offline"
 * here only in the sense that nothing more will arrive.
 */
export function useRunStream(id: string, active: boolean): StreamStatus {
  const qc = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>("offline");
  const lastSeq = useRef(0);

  useEffect(() => {
    if (!active) {
      setStatus("offline");
      return;
    }
    const source = new EventSource(`/api/runs/${id}/events/stream`);
    source.onopen = () => setStatus("live");
    source.onerror = () => {
      // EventSource reconnects by itself with Last-Event-ID; closed means it gave up.
      setStatus(source.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
    };
    const onEvent = (e: MessageEvent) => {
      const event = JSON.parse(e.data) as EventView;
      if (event.seq <= lastSeq.current) return;
      lastSeq.current = event.seq;
      qc.setQueryData(keys.events(id), (old: { chain_ok: boolean; events: EventView[] } | undefined) =>
        old && !old.events.some((x) => x.seq === event.seq)
          ? { ...old, events: [...old.events, event] }
          : old,
      );
      if (event.event_type === "transition") {
        qc.invalidateQueries({ queryKey: keys.run(id) });
        qc.invalidateQueries({ queryKey: ["runs"] });
      }
    };
    // Each event arrives under its own type name (calipa.api.app._sse).
    const listener = (e: Event) => onEvent(e as MessageEvent);
    for (const type of EVENT_TYPES) source.addEventListener(type, listener);
    // "end": the run finished or now waits for a person; nothing more will come.
    source.addEventListener("end", () => {
      source.close();
      setStatus("offline");
      qc.invalidateQueries({ queryKey: keys.run(id) });
    });
    return () => source.close();
  }, [id, active, qc]);

  return status;
}

function useRunMutation<V>(id: string, fn: (v: V) => Promise<RunView>) {
  const qc = useQueryClient();
  return useMutation<RunView, ApiError, V>({
    mutationFn: fn,
    onSuccess: (run) => {
      qc.setQueryData(keys.run(id), run);
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: keys.events(id) });
    },
  });
}

export function useAnswer(id: string) {
  return useRunMutation(id, (answer: string) => api.answer(id, answer));
}

export function useReview(id: string) {
  return useRunMutation(
    id,
    (body: { decision: ReviewDecision; report_sha256: string; note: string; acknowledge_flags: boolean }) =>
      api.review(id, body),
  );
}

export function useCreateRun() {
  const qc = useQueryClient();
  return useMutation<RunView, ApiError, { text: string; key: string }>({
    mutationFn: ({ text, key }) => api.createRun(text, key),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
}
