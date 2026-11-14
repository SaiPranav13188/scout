import { expect, test, type Page } from "@playwright/test";

// Seed data (e2e/stack.py): three fresher AI jobs at "Acme AI"; "GenAI Engineer - New Grad" is
// applied with a follow-up due; "AI Engineer" has a tailored resume; resume v1 is not reviewed yet.

const API = "http://localhost:8021";
const TOKEN = "e2e-dev-token";

// Every test fails if the page throws an uncaught error.
let pageErrors: string[] = [];
test.beforeEach(async ({ page }) => {
  pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));
});
test.afterEach(() => {
  expect(pageErrors, "uncaught page errors").toEqual([]);
});

test("Today lists confirmed fresher AI jobs", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("AI Engineer", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Machine Learning Engineer (Fresher)").first()).toBeVisible();
});

test("job panel: tailored resume downloads as PDF and drafts are offered", async ({ page }) => {
  await page.goto("/");
  await page.getByText("AI Engineer", { exact: true }).first().click();
  await expect(page.getByText("Tailored resume", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "PDF" }).first().click();
  expect((await download).suggestedFilename()).toMatch(/\.pdf$/);
  await expect(page.getByRole("button", { name: "Draft messages" })).toHaveCount(1);
});

test("Pipeline: moving a card to Interview is saved in the API", async ({ page, request }) => {
  await page.goto("/pipeline");
  await expect(page.getByText("follow-up is due")).toBeVisible();
  await page.getByLabel("Status of GenAI Engineer - New Grad").selectOption("interview");
  await expect(page.getByRole("region", { name: "Interview" })).toContainText("GenAI Engineer");
  await expect(async () => {
    const apps = await (await request.get(`${API}/api/applications`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    })).json();
    expect(apps[0]?.status).toBe("interview");
  }).toPass({ timeout: 10_000 });
});

test("Resume: reviewing saves a new version; autofill profile offered", async ({ page }) => {
  await page.goto("/resume");
  await expect(page.getByText("not reviewed yet")).toBeVisible();
  await expect(page.getByRole("button", { name: "Autofill profile" })).toHaveCount(1);
  await page.getByRole("button", { name: "Looks right, mark reviewed" }).click();
  await expect(page.getByText("Saved as version 2")).toBeVisible();
});

test("Agent health shows workers and next runs", async ({ page }) => {
  await page.goto("/health");
  await expect(page.getByText("Gemini usage, last 24 h")).toBeVisible();
  await expect(page.getByText("Job ingestion").first()).toBeVisible();
  await expect(page.getByText("Next run").first()).toBeVisible();
});

test("Settings has editable score bands", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByText("Match score", { exact: true })).toBeVisible();
  await expect(page.getByText("Top match from")).toHaveCount(1);
});

async function noSidewaysScroll(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width, `page width on ${path}`).toBeLessThanOrEqual(390);
}

test.describe("phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  for (const path of ["/", "/pipeline", "/resume", "/health", "/settings"]) {
    test(`no sideways scroll: ${path}`, async ({ page }) => {
      await noSidewaysScroll(page, path);
    });
  }
});

test("Today: jobs skipped quickly one after another never come back (slow saves)", async ({ page, request }) => {
  // 2026-11-14: a list refresh started by the first click finished before the second click was saved and put
  // that job back on screen. Saves are slowed here so the refresh always overlaps the next click.
  await page.route("**/api/jobs/*/status", async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.goto("/");
  const titles = ["AI Engineer", "Machine Learning Engineer (Fresher)"];
  await expect(page.getByText(titles[0], { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  const ids: string[] = [];
  page.on("request", (r) => {
    const m = r.url().match(/\/api\/jobs\/([^/]+)\/status$/);
    if (m && r.method() === "POST") ids.push(m[1]);
  });
  const seen: string[] = [];
  for (const [i, t] of titles.entries()) {
    const card = page.locator("article", { hasText: t }).first();
    await card.getByRole("button", { name: "Skip" }).click();
    if (i < titles.length - 1) await page.waitForTimeout(300);
  }
  // Watch the whole time both saves and their refreshes run: a skipped job must not flash back even briefly.
  for (let n = 0; n < 50; n++) {
    for (const t of titles) {
      if (await page.locator("article", { hasText: t }).count()) seen.push(`${t} @ ${n * 100}ms`);
    }
    await page.waitForTimeout(100);
  }
  expect(seen, "skipped jobs shown again").toEqual([]);
  // Put the seed back for any later test.
  for (const id of ids) {
    await request.post(`${API}/api/jobs/${id}/status`, {
      headers: { Authorization: `Bearer ${TOKEN}` }, data: { status: "discovered" } });
  }
});
