import { expect, test } from "@playwright/test";

for (const mixed of [true, false]) {
  test(`dashboard respects API currency instead of the client default: ${mixed ? "mixed" : "mismatch"}`, async ({ page }) => {
    const currency = mixed ? null : "USD";
    const spend = mixed ? null : 111;
    await page.route("**/api/backend/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api/backend", "");
      let body: unknown = { items: [] };
      if (path === "/auth/me") body = {
        user: { id: "dashboard-admin", role: "admin", status: "active", name: "Dashboard admin" },
        session: { valid: true, role: "admin", user_id: "dashboard-admin", global_access: true, accessible_client_ids: ["legacy"] },
      };
      else if (path === "/clients") body = { items: [{ id: "legacy", name: "Legacy KZT default", default_currency: "KZT", status: "active" }] };
      else if (path === "/agency/overview") body = { per_client: [{ client_id: "legacy", currency, spend }], per_account: [] };
      else if (path === "/insights/overview") body = {
        range: { date_from: "2026-09-01", date_to: "2026-09-02", as_of_date: "2026-09-02" },
        scope: { client_id: null, account_id: null },
        data_quality: { status: "fresh", rows_present: true, row_count: 2 },
        spend_summary: { spend, currency, conversions: 1 },
        budget_summary: { budget: null, spend: null, pace_status: "no_budget", unavailable_reason: mixed ? "mixed_currencies" : "currency_mismatch" },
        breakdowns: {
          platforms: [{ platform: "meta", spend: 111, currency: "USD", conversions: 1 }],
          accounts: [{ account_id: "usd-account", client_id: "legacy", platform: "meta", name: "Native USD account", spend: 111, currency: "USD", conversions: 1 }],
        },
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/?admin_metrics=1");
    const spendCard = page.locator(".kpi-card").filter({ hasText: "Расход за период" });
    await expect(spendCard.locator(".kpi-value")).toHaveText(mixed ? "Разные валюты" : /111\s*\$/);
    const chartUnavailable = page.getByText("Выберите клиента с одной валютой для графика расходов.", { exact: true });
    if (mixed) await expect(chartUnavailable).toBeVisible();
    else await expect(chartUnavailable).toHaveCount(0);
    const accountRow = page.getByRole("row").filter({ hasText: "Native USD account" });
    await expect(accountRow).toContainText("$");
    await expect(accountRow).not.toContainText(/KZT|₸/);
  });
}
