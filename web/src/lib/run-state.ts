// The run lifecycle as the UI presents it. Mirrors calipa.orchestrator.lifecycle.RunState.

export const RUN_STATES = [
  "RECEIVED",
  "INTERPRETING",
  "CLARIFICATION_REQUESTED",
  "EXTRACTING",
  "EXTRACTION_PARTIAL",
  "RECONCILING",
  "NO_EXCEPTIONS",
  "ANALYSING",
  "REPORTING",
  "PENDING_REVIEW",
  "DENIED",
  "UNSUPPORTED",
  "ESCALATED",
  "FAILED",
  "APPROVED",
  "REJECTED",
  "REINVESTIGATION",
] as const;

export type RunState = (typeof RUN_STATES)[number];

/**
 * Families decide the look:
 * - working: Calipa is busy, nobody needs to act (live pulse)
 * - needs-you: waiting on a person (clarify, review)
 * - done: finished cleanly
 * - stopped: ended without a result a person can sign off
 * - failed: something broke
 */
export type RunFamily = "working" | "needs-you" | "done" | "stopped" | "failed";

export const RUN_STATE_META: Record<RunState, { label: string; family: RunFamily; hint: string }> = {
  RECEIVED: { label: "Queued", family: "working", hint: "Waiting for a worker to pick it up" },
  INTERPRETING: { label: "Reading request", family: "working", hint: "P1 is turning the request into a scope" },
  CLARIFICATION_REQUESTED: { label: "Needs your answer", family: "needs-you", hint: "The request was unclear; answer to continue" },
  EXTRACTING: { label: "Fetching data", family: "working", hint: "Reading transfers and rates from the bank" },
  EXTRACTION_PARTIAL: { label: "Partial data", family: "needs-you", hint: "Some data could not be fetched" },
  RECONCILING: { label: "Reconciling", family: "working", hint: "Running the reconciliation rules" },
  NO_EXCEPTIONS: { label: "No exceptions", family: "done", hint: "Every check passed" },
  ANALYSING: { label: "Analysing", family: "working", hint: "P2 is explaining each exception" },
  REPORTING: { label: "Writing report", family: "working", hint: "P3 is writing; V3 checks every figure" },
  PENDING_REVIEW: { label: "Awaiting review", family: "needs-you", hint: "A second person must approve" },
  DENIED: { label: "Not permitted", family: "stopped", hint: "Outside what this user may audit" },
  UNSUPPORTED: { label: "Not supported", family: "stopped", hint: "Calipa cannot audit this yet" },
  ESCALATED: { label: "Escalated", family: "stopped", hint: "Handed to a person to decide" },
  FAILED: { label: "Failed", family: "failed", hint: "The run stopped on an error" },
  APPROVED: { label: "Approved", family: "done", hint: "Signed off by a reviewer" },
  REJECTED: { label: "Rejected", family: "failed", hint: "The reviewer rejected the report" },
  REINVESTIGATION: { label: "Reinvestigating", family: "working", hint: "Sent back for another look" },
};
