import { expect, test } from "@playwright/test";

for (const reason of ["mixed_currencies", "currency_mismatch"] as const) {
  test(`client portal explains unavailable budget comparison: ${reason}`, async ({ page }) => {
    const mixed = reason === "mixed_currencies";
    await page.route("**/api/backend/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api/backend", "");
      let body: unknown = { items: [] };
      if (path === "/auth/me") body = {
        user: { id: "legacy-user", name: "Legacy user", role: "client", status: "active" },
        session: { valid: true, user_id: "legacy-user", role: "client", accessible_client_ids: ["legacy-client"] },
      };
      else if (path === "/clients") body = { items: [{ id: "legacy-client", name: "Legacy client", status: "active", default_currency: "KZT" }] };
      else if (path === "/insights/overview") body = {
        range: { date_from: "2026-09-01", date_to: "2026-09-02", as_of_date: "2026-09-02", timezone_policy: "UTC" },
        scope: { client_id: "legacy-client", account_id: null },
        spend_summary: { spend: mixed ? null : 111, currency: mixed ? null : "USD", conversions: 1 },
        budget_summary: { budget: null, spend: null, pace_status: "no_budget", unavailable_reason: reason },
        breakdowns: { platforms: [], accounts: [] },
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/portal");
    const note = mixed ? "Расходы включают разные валюты." : "Валюты расхода и бюджета различаются.";
    await expect(page.locator(".warning").filter({ hasText: note })).toBeVisible();
    const spendCard = page.locator(".kpi-card").filter({ has: page.locator(".kpi-title", { hasText: /^Расход$/ }) }).first();
    await expect(spendCard.locator(".kpi-value")).toHaveText(mixed ? "Разные валюты" : /111\s*\$/);
    await expect(page.getByText("Сравнение недоступно", { exact: true }).first()).toBeVisible();
  });
}

test("late client response cannot replace a newly selected client's data", async ({ page }) => {
  const clients = [
    { id: "client-a", name: "Race Client A", status: "active", default_currency: "USD" },
    { id: "client-b", name: "Race Client B", status: "active", default_currency: "KZT" },
  ];
  let holdClientA = false;
  let releaseA = () => {};
  let signalHeld = () => {};
  const held = new Promise<void>((resolve) => { signalHeld = resolve; });
  const release = new Promise<void>((resolve) => { releaseA = resolve; });
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/backend", "");
    const clientId = url.searchParams.get("client_id") || "client-a";
    const selected = clients.find((client) => client.id === clientId)!;
    let body: unknown = { items: [] };
    if (path === "/auth/me") {
      body = {
        user: { id: "race-user", name: "Race user", email: "race@example.test", role: "client", status: "active" },
        session: { valid: true, user_id: "race-user", role: "client", global_access: false, accessible_client_ids: clients.map((client) => client.id) },
      };
    } else if (path === "/clients") body = { items: clients };
    else if (path === "/ad-accounts") {
      body = { items: [{ id: `account-${clientId}`, client_id: clientId, name: `Account ${selected.name}`, platform: "meta", currency: selected.default_currency, status: "active" }] };
    } else if (path === "/insights/overview") {
      if (holdClientA && clientId === "client-a") { signalHeld(); await release; }
      body = {
        range: { date_from: "2026-09-01", date_to: "2026-09-02", as_of_date: "2026-09-02", timezone_policy: "UTC" },
        scope: { client_id: clientId, account_id: null },
        spend_summary: { spend: clientId === "client-a" ? 111 : 222, currency: selected.default_currency, conversions: 1 },
        budget_summary: { budget: 1000, pace_status: "on_track" },
        breakdowns: { platforms: [], accounts: [] },
      };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/portal");
  await expect(page.getByText("Account Race Client A", { exact: true }).first()).toBeVisible();
  holdClientA = true;
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await held;
  await page.locator("select").filter({ has: page.locator('option[value="client-b"]') }).selectOption("client-b");
  await expect(page.getByText("Account Race Client A", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Account Race Client B", { exact: true }).first()).toBeVisible();
  const completed = page.waitForResponse((response) => response.url().includes("/insights/overview?") && response.url().includes("client-a"));
  releaseA();
  await (await completed).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByText("Account Race Client A", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Account Race Client B", { exact: true }).first()).toBeVisible();
});
