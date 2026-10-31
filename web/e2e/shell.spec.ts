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
