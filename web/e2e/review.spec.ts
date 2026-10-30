import { expect, seeded, test } from "./fixtures";

test.describe("second-person review", () => {
  test("the queue lists reports waiting for the reviewer", async ({ page, signIn }) => {
    await signIn("reviewer@bank");
    await page.goto("/review");
    for (const id of [seeded.pending, seeded.approve, seeded.flagged]) {
      await expect(page.getByRole("link", { name: new RegExp(id) })).toBeVisible();
    }
  });

  test("the author cannot review their own report", async ({ page, signIn }) => {
    await signIn("auditor@bank");
    await page.goto(`/runs/${seeded.pending}`);
    await page.getByRole("tab", { name: "Review" }).click();
    await expect(page.getByText("Waiting for a reviewer")).toBeVisible();
    await expect(page.getByRole("radiogroup", { name: "Decision" })).toHaveCount(0);
  });

  test("approving is confirmed first and recorded once, however often the button is pressed", async ({ page, signIn, api }) => {
    await signIn("reviewer@bank");
    await page.goto(`/runs/${seeded.approve}`);

    await page.getByRole("radio", { name: "Approve" }).click();
    await page.getByRole("button", { name: "Approve…" }).click();
    const dialog = page.getByRole("dialog", { name: "Approve this report?" });
    await expect(dialog).toBeVisible();
    const run = await api.run(seeded.approve);
    await expect(dialog).toContainText(run.report!.report_sha256.slice(0, 12));

    await dialog.getByRole("button", { name: "Approve report" }).dblclick();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("by reviewer@bank")).toBeVisible();
    await expect(page.getByText("Someone got there first")).toHaveCount(0);

    expect((await api.run(seeded.approve)).state).toBe("APPROVED");
    const approvals = (await api.events(seeded.approve)).filter((e) => e.state === "APPROVED");
    expect(approvals).toHaveLength(1);
  });

  test("cancelling the confirmation records nothing", async ({ page, signIn, api }) => {
    await signIn("reviewer@bank");
    await page.goto(`/runs/${seeded.pending}`);
    await page.getByRole("radio", { name: "Approve" }).click();
    await page.getByRole("button", { name: "Approve…" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("dialog")).toBeHidden();
    expect((await api.run(seeded.pending)).state).toBe("PENDING_REVIEW");
  });

  test("sending back needs a reason and opens the new run", async ({ page, signIn, api }) => {
    await signIn("reviewer@bank");
    await page.goto(`/runs/${seeded.send_back}`);

    await page.getByRole("radio", { name: "Send back" }).click();
    const submit = page.getByRole("button", { name: "Send back…" });
    await expect(submit).toBeDisabled();
    await page.getByLabel("Reason (required)").fill("The fee legs on the 25th need a second look.");
    await submit.click();

    const dialog = page.getByRole("dialog", { name: "Send back for another look?" });
    await expect(dialog).toContainText("The fee legs on the 25th need a second look.");
    await dialog.getByRole("button", { name: "Send back" }).click();
    await expect(page.getByRole("main").getByText("Sent back for re-investigation")).toBeVisible();

    const child = (await api.run(seeded.send_back)).review!.child_run_id!;
    await page.getByRole("link", { name: "Open the re-investigation" }).click();
    await expect(page).toHaveURL(`/runs/${child}`);
  });

  test("a flagged report is approved only once the flags are acknowledged", async ({ page, signIn, api }) => {
    await signIn("reviewer@bank");
    await page.goto(`/runs/${seeded.flagged}`);

    await page.getByRole("radio", { name: "Approve" }).click();
    const submit = page.getByRole("button", { name: "Approve…" });
    await expect(submit).toBeDisabled();
    await page.getByRole("checkbox").click();
    await submit.click();
    await page.getByRole("dialog").getByRole("button", { name: "Approve report" }).click();
    await expect(page.getByText("by reviewer@bank")).toBeVisible();

    expect((await api.run(seeded.flagged)).review?.flags_acknowledged).toBe(true);
  });
});
