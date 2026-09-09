import { expect, test } from "@playwright/test";

test("agency report groups currencies and calculates shares only within one currency", async ({ page }) => {
  const clients = [
    { id: "a", name: "USD A", default_currency: "USD", status: "active" },
    { id: "b", name: "KZT B", default_currency: "KZT", status: "active" },
    { id: "c", name: "USD C", default_currency: "USD", status: "active" },
  ];
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/backend", "");
    let body: unknown = { items: [] };
    if (path === "/auth/me") {
      body = {
        user: { id: "report-admin", role: "admin", status: "active", name: "Report admin" },
        session: { valid: true, role: "admin", user_id: "report-admin", global_access: true, accessible_client_ids: ["a", "b", "c"] },
      };
    } else if (path === "/clients") body = { items: clients };
    else if (path === "/agency/overview") {
      body = {
        totals: { spend: null, currency: null },
        totals_by_currency: [{ currency: "USD", spend: 400 }, { currency: "KZT", spend: 50000 }],
        per_client: [
          { client_id: "a", currency: "USD", spend: 100 },
          { client_id: "b", currency: "KZT", spend: 50000 },
          { client_id: "c", currency: "USD", spend: 300 },
        ],
        per_account: [],
      };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/agency/reports");
  const usdA = page.getByRole("row").filter({ hasText: "USD A" });
  const kztB = page.getByRole("row").filter({ hasText: "KZT B" });
  const usdC = page.getByRole("row").filter({ hasText: "USD C" });
  await expect(usdA).toContainText("25.0%");
  await expect(usdC).toContainText("75.0%");
  await expect(kztB).toContainText("100.0%");
  await expect(usdA).toContainText("$");
  await expect(kztB).toContainText(/KZT|₸/);
  const summary = page.locator(".kpi-card").filter({ hasText: "Расход по валютам" });
  await expect(summary).toContainText(/400\s*\$/);
  await expect(summary).toContainText(/50[\s\u00a0]000\s*(KZT|₸)/);
});

for (const role of ["admin", "agency"] as const) {
  test(`legacy mixed-client components remain visible in ${role} report totals`, async ({ page }) => {
    const clients = [
      { id: "mixed", name: "Mixed legacy client", default_currency: "USD", status: "active" },
      { id: "other", name: "Other agency client", default_currency: "USD", status: "active" },
    ];
    await page.route("**/api/backend/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api/backend", "");
      let body: unknown = { items: [] };
      if (path === "/auth/me") body = {
        user: { id: "currency-user", role, status: "active", name: "Currency user" },
        session: { valid: true, role, user_id: "currency-user", global_access: role === "admin", accessible_client_ids: ["mixed", "other"] },
      };
      else if (path === "/platform/agencies") body = { items: [{ id: "selected-agency", name: "Selected", status: "active" }] };
      else if (path === "/platform/agencies/selected-agency/clients") body = [{ agency_id: "selected-agency", client_id: "mixed", status: "active" }];
      else if (path === "/platform/agencies/selected-agency/members") body = [{ user_id: "currency-user", agency_id: "selected-agency", role: "owner", status: "active" }];
      else if (path === "/clients") body = { items: clients };
      else if (path === "/agency/overview") body = {
        totals: { spend: null, currency: null },
        totals_by_currency: [{ currency: "USD", spend: 919 }, { currency: "KZT", spend: 10000 }],
        per_client: [{ client_id: "mixed", spend: null, currency: null }, { client_id: "other", spend: 899, currency: "USD" }],
        per_account: [
          { account_id: "usd", client_id: "mixed", spend: 20, currency: "USD" },
          { account_id: "kzt", client_id: "mixed", spend: 10000, currency: "KZT" },
          { account_id: "outside", client_id: "other", spend: 899, currency: "USD" },
        ],
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/agency/reports");
    const summary = page.locator(".kpi-card").filter({ hasText: "Расход по валютам" });
    await expect(summary).toContainText(role === "admin" ? /919\s*\$/ : /20\s*\$/);
    await expect(summary).toContainText(/10[\s\u00a0]000\s*(KZT|₸)/);
    await expect(page.getByRole("row").filter({ hasText: "Mixed legacy client" })).toContainText("Разные валюты");
    if (role === "agency") {
      await expect(page.getByRole("row").filter({ hasText: "Other agency client" })).toHaveCount(0);
      await expect(summary).not.toContainText("919");
    }
  });
}
