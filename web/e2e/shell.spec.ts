import { expect, seeded, test } from "./fixtures";

test.describe("finding your way", () => {
  test("search finds an audit by its request and opens it", async ({ page, signIn }) => {
    await signIn("auditor@bank");
    await page.goto("/runs");
    await page.keyboard.press("ControlOrMeta+k");
    await page.getByPlaceholder(/Search audits/).fill("all of 2025 every account");
    await expect(page.getByRole("option")).toHaveCount(1);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`/runs/${seeded.question}`);
  });

  test("the state bar splits every audit and filters like its tile", async ({ page, signIn }) => {
    await signIn("auditor@bank");
    await page.goto("/runs");
    const parts = page.getByRole("group", { name: "Audits by state" }).getByRole("button");
    await expect(parts.first()).toBeVisible();
    const labels = await parts.evaluateAll((b) => b.map((x) => x.getAttribute("aria-label") ?? ""));
    const total = labels.reduce((a, l) => a + Number(l.split(" ")[0]), 0);
    await expect(page.getByRole("tab", { name: new RegExp(`^All\\s*${total}$`) })).toBeVisible();
    await parts.first().click();
    await expect(page.getByRole("button", { pressed: true })).toHaveCount(1);
  });

  test("controls and access lists every control Calipa runs", async ({ page, signIn, request }) => {
    const reference = await (await request.get("http://localhost:8766/reference", { headers: { "X-Calipa-User": "auditor@bank" } })).json();
    await signIn("auditor@bank");
    await page.goto("/controls");
    await expect(page.getByRole("heading", { name: "Controls and access" })).toBeVisible();
    for (const c of reference.controls as { control_id: string }[]) {
      await expect(page.locator(`[id="${c.control_id}"]`)).toBeVisible();
    }
  });

  test("an unknown address says there is no page", async ({ page, signIn }) => {
    await signIn("auditor@bank");
    await page.goto("/no-such-page");
    await expect(page.getByText("There is no page here")).toBeVisible();
  });
});
