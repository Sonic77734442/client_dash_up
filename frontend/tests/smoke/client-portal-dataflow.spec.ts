import { expect, test } from "@playwright/test";
import { attachSession, createAdminSession, createClientSessionForExistingClient } from "./auth";

function isoDate(daysFromNow = 0) {
  const d = new Date();
  d.setDate(d.getDate() + daysFromNow);
  return d.toISOString().slice(0, 10);
}

test("client portal shows agency-assigned accounts, budgets and report data", async ({ page, context, request }) => {
  const adminToken = await createAdminSession(request);
  const adminAuth = { Authorization: `Bearer ${adminToken}` };

  const clientRes = await request.post("http://127.0.0.1:8000/clients", {
    headers: adminAuth,
    data: { name: `portal-tenant-${Date.now()}`, status: "active", default_currency: "USD" },
  });
  expect(clientRes.ok()).toBeTruthy();
  const tenant = (await clientRes.json()) as { id: string };

  const accountName = "Portal Visible Account";
  const accountRes = await request.post("http://127.0.0.1:8000/ad-accounts", {
    headers: adminAuth,
    data: {
      client_id: tenant.id,
      platform: "meta",
      external_account_id: `portal-${Date.now()}`,
      name: accountName,
      currency: "USD",
      status: "active",
    },
  });
  expect(accountRes.ok()).toBeTruthy();
  const account = (await accountRes.json()) as { id: string };

  const budgetRes = await request.post("http://127.0.0.1:8000/budgets", {
    headers: adminAuth,
    data: {
      client_id: tenant.id,
      scope: "account",
      account_id: account.id,
      amount: "1200.00",
      currency: "USD",
      period_type: "custom",
      start_date: isoDate(-15),
      end_date: isoDate(15),
      note: "portal smoke budget",
    },
  });
  expect(budgetRes.ok()).toBeTruthy();

  const ingestRes = await request.post("http://127.0.0.1:8000/ad-stats/ingest", {
    headers: { ...adminAuth, "Idempotency-Key": `portal-smoke-${Date.now()}` },
    data: {
      rows: [
        {
          ad_account_id: account.id,
          date: isoDate(0),
          platform: "meta",
          impressions: 10000,
          clicks: 250,
          spend: "245.00",
          conversions: "12.00",
        },
      ],
    },
  });
  expect(ingestRes.ok()).toBeTruthy();

  const clientToken = await createClientSessionForExistingClient(request, tenant.id);
  await attachSession(page, context, clientToken);

  await page.goto("/portal");
  await expect(page.getByText("Client Overview")).toBeVisible();
  await expect(page.getByText(accountName)).toBeVisible();
  await expect(page.getByText("$1,200")).toBeVisible();

  await page.goto("/portal/reports");
  await expect(page.getByText("Client Reports")).toBeVisible();
  await expect(page.getByText("Account Performance")).toBeVisible();
  await expect(page.getByText(accountName)).toBeVisible();
  await expect(page.getByText("$245").first()).toBeVisible({ timeout: 10000 });

  await page.goto("/portal/billing");
  await expect(page.getByText("Client Billing")).toBeVisible();
  await expect(page.getByText("Billing Scope")).toBeVisible();
  await expect(page.getByText("$1,200")).toBeVisible();
});
