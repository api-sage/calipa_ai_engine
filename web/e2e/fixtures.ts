import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test as base, expect, type APIRequestContext } from "@playwright/test";
import type { EventView, RunView } from "@/lib/api/client";
import { USER_COOKIE } from "@/lib/users";

type Seeded = Record<"pending" | "approve" | "send_back" | "flagged" | "question", string>;

/** The run ids tests/e2e_seed.py wrote before the API started. */
export const seeded: Seeded = JSON.parse(readFileSync(join(__dirname, ".ids.json"), "utf8"));

const API = "http://localhost:8766";

/** Reads the API directly, to check what the browser actually caused. */
export class Api {
  constructor(private readonly request: APIRequestContext) {}

  private async get<T>(path: string, user: string): Promise<T> {
    const res = await this.request.get(`${API}${path}`, { headers: { "X-Calipa-User": user } });
    expect(res.ok(), `GET ${path}`).toBeTruthy();
    return res.json() as Promise<T>;
  }

  run(id: string, user = "lead@bank") {
    return this.get<RunView>(`/runs/${id}`, user);
  }

  async events(id: string, user = "lead@bank") {
    return (await this.get<{ events: EventView[] }>(`/runs/${id}/events`, user)).events;
  }

  async mine(user: string) {
    return this.get<{ run_id: string }[]>(`/runs?view=mine&limit=200`, user);
  }
}

export const test = base.extend<{ signIn: (user: string) => Promise<void>; api: Api }>({
  // The development sign-in: the same cookie the user switch writes.
  signIn: async ({ context, baseURL }, use) => {
    await use(async (user) => {
      await context.addCookies([{ name: USER_COOKIE, value: encodeURIComponent(user), url: baseURL! }]);
    });
  },
  api: async ({ request }, use) => {
    await use(new Api(request));
  },
});

export { expect };
