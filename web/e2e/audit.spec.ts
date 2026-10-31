import { expect, seeded, test } from "./fixtures";

test.describe("starting and steering an audit", () => {
  test("starting an audit is confirmed first, then opens it", async ({ page, signIn, api }) => {
    const request = `Check duplicates on 25 October 2026 (${Date.now()})`;
    await signIn("auditor@bank");
    await page.goto("/runs/new");
    const before = (await api.mine("auditor@bank")).length;

    await page.getByLabel("What should Calipa check?").fill(request);
    await page.getByRole("button", { name: "Start audit" }).click();
    const dialog = page.getByRole("dialog", { name: "Start this audit?" });
    await expect(dialog).toContainText(request);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect((await api.mine("auditor@bank")).length).toBe(before);

    await page.getByRole("button", { name: "Start audit" }).click();
    await dialog.getByRole("button", { name: "Start audit" }).dblclick();
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(request);
    expect((await api.mine("auditor@bank")).length).toBe(before + 1);
  });

  test("someone who only reviews cannot start an audit", async ({ page, signIn }) => {
    await signIn("reviewer@bank");
    await page.goto("/runs/new");
    await expect(page.getByText("You review audits; you don't start them")).toBeVisible();
  });

  test("answering Calipa's question hands the audit back, and says when no worker picks it up", async ({ page, signIn, api }) => {
    await page.clock.install();
    await signIn("auditor@bank");
    await page.goto(`/runs/${seeded.question}`);

    await expect(page.getByText("which 92 days should I audit?")).toBeVisible();
    await page.getByLabel("Your answer").fill("The first 30 days of 2025.");
    await page.getByRole("button", { name: "Send answer" }).dblclick();
    await expect(page.getByText("Reading request").first()).toBeVisible();

    const answers = (await api.events(seeded.question, "auditor@bank")).filter((e) => e.event_type === "clarification_answered");
    expect(answers).toHaveLength(1);

    // No worker runs in these tests: after 30 seconds the page says so instead of spinning.
    await page.clock.fastForward("00:45");
    await expect(page.getByText("Waiting for a worker to pick this up")).toBeVisible();
  });
});
