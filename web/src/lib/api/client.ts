import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["Me"];
export type RunView = S["RunView"];
export type RunListItem = S["RunListItem"];
export type EventView = S["EventView"];
export type EventsView = S["EventsView"];
export type ReportView = S["ReportView"];
export type ReportDetail = S["ReportDetail"];
export type FindingDetail = S["FindingDetail"];
export type ExceptionView = S["ExceptionView"];
export type HypothesisView = S["HypothesisView"];
export type ClaimView = S["ClaimView"];
export type MoneyView = S["MoneyView"];
export type ReviewDecision = S["ReviewRequest"]["decision"];
export type ReferenceView = S["ReferenceView"];
export type ControlView = S["ControlView"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }

  /** Words a person can act on, for each way the API refuses. */
  get title(): string {
    switch (this.status) {
      case 401:
        return "You are not signed in";
      case 403:
        return "You do not have access";
      case 404:
        return "Not found";
      case 409:
        return "Someone got there first";
      case 422:
        return "That was not accepted";
      default:
        return this.status >= 500 ? "Calipa had a problem" : "Request failed";
    }
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d[0] && typeof d[0] === "object" && "msg" in d[0]) return String(d[0].msg);
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Calipa could not be reached. Check that the API is running.");
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detail(body, res.statusText));
  return body as T;
}

export const api = {
  me: () => request<Me>("/me"),
  reference: () => request<ReferenceView>("/reference"),
  runs: (view: "mine" | "review_queue", limit = 200) => request<RunListItem[]>(`/runs?view=${view}&limit=${limit}`),
  run: (id: string) => request<RunView>(`/runs/${id}`),
  events: (id: string) => request<EventsView>(`/runs/${id}/events`),
  report: (id: string) => request<ReportView>(`/runs/${id}/report`),
  createRun: (request_text: string, idempotency_key: string) =>
    request<RunView>("/runs", { method: "POST", body: JSON.stringify({ request_text, idempotency_key }) }),
  answer: (id: string, answer: string) =>
    request<RunView>(`/runs/${id}/clarification`, { method: "POST", body: JSON.stringify({ answer }) }),
  review: (
    id: string,
    body: { decision: ReviewDecision; report_sha256: string; note: string; acknowledge_flags: boolean },
  ) => request<RunView>(`/runs/${id}/review`, { method: "POST", body: JSON.stringify(body) }),
};
