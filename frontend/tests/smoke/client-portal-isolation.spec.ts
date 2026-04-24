import { expect, test } from "@playwright/test";
import {
  attachSession,
  createAdminSession,
  createClientSessionForExistingClient,
} from "./auth";

function isoDate(daysFromNow = 0) {
  const d = new Date();
  d.setDate(d.getDate() + daysFromNow);
  return d.toISOString().slice(0, 10);
}

test("client portal isolates data between two client tenants", async ({ browser, request }) => {
  const adminToken = await createAdminSession(request);
  const adminAuth = { Authorization: `Bearer ${adminToken}` };

  const c1Res = await request.post("http://127.0.0.1:8000/clients", {
    headers: adminAuth,
    data: { name: `iso-c1-${Date.now()}`, status: "active", default_currency: "USD" },
  });
  expect(c1Res.ok()).toBeTruthy();
  const c1 = (await c1Res.json()) as { id: string };

  const c2Res = await request.post("http://127.0.0.1:8000/clients", {
    headers: adminAuth,
    data: { name: `iso-c2-${Date.now()}`, status: "active", default_currency: "USD" },
  });
  expect(c2Res.ok()).toBeTruthy();
  const c2 = (await c2Res.json()) as { id: string };

  const c1AccountName = "ISO Client One Account";
  const c2AccountName = "ISO Client Two Account";

  const c1AccountRes = await request.post("http://127.0.0.1:8000/ad-accounts", {
    headers: adminAuth,
    data: {
      client_id: c1.id,
      platform: "meta",
      external_account_id: `iso-shared-${Date.now()}`,
      name: c1AccountName,
      currency: "USD",
      status: "active",
    },
  });
  expect(c1AccountRes.ok()).toBeTruthy();
  const c1Account = (await c1AccountRes.json()) as { id: string };

  const c2AccountRes = await request.post("http://127.0.0.1:8000/ad-accounts", {
    headers: adminAuth,
    data: {
      client_id: c2.id,
      platform: "meta",
      external_account_id: `iso-shared-${Date.now()}`,
      name: c2AccountName,
      currency: "USD",
      status: "active",
    },
  });
  expect(c2AccountRes.ok()).toBeTruthy();
  const c2Account = (await c2AccountRes.json()) as { id: string };

  const budgetStart = isoDate(-15);
  const budgetEnd = isoDate(15);
  const c1Budget = "1111.00";
  const c2Budget = "2222.00";

  const c1BudgetRes = await request.post("http://127.0.0.1:8000/budgets", {
    headers: adminAuth,
    data: {
      client_id: c1.id,
      scope: "account",
      account_id: c1Account.id,
      amount: c1Budget,
      currency: "USD",
      period_type: "custom",
      start_date: budgetStart,
      end_date: budgetEnd,
    },
  });
  expect(c1BudgetRes.ok()).toBeTruthy();

  const c2BudgetRes = await request.post("http://127.0.0.1:8000/budgets", {
    headers: adminAuth,
    data: {
      client_id: c2.id,
      scope: "account",
      account_id: c2Account.id,
      amount: c2Budget,
      currency: "USD",
      period_type: "custom",
      start_date: budgetStart,
      end_date: budgetEnd,
    },
  });
  expect(c2BudgetRes.ok()).toBeTruthy();

  const ingestRes = await request.post("http://127.0.0.1:8000/ad-stats/ingest", {
    headers: { ...adminAuth, "Idempotency-Key": `portal-isolation-${Date.now()}` },
    data: {
      rows: [
        {
          ad_account_id: c1Account.id,
          date: isoDate(0),
          platform: "meta",
          impressions: 1111,
          clicks: 111,
          spend: "111.00",
          conversions: "11.00",
        },
        {
          ad_account_id: c2Account.id,
          date: isoDate(0),
          platform: "meta",
          impressions: 2222,
          clicks: 222,
          spend: "222.00",
          conversions: "22.00",
        },
      ],
    },
  });
  expect(ingestRes.ok()).toBeTruthy();

  const c1Token = await createClientSessionForExistingClient(request, c1.id);
  const c2Token = await createClientSessionForExistingClient(request, c2.id);

  const c1Context = await browser.newContext();
  const c1Page = await c1Context.newPage();
  await attachSession(c1Page, c1Context, c1Token);
  await c1Page.goto("/portal");
  await expect(c1Page.getByText(c1AccountName)).toBeVisible();
  await expect(c1Page.getByText(c2AccountName)).toHaveCount(0);
  await expect(c1Page.getByText("$1,111")).toBeVisible();
  await expect(c1Page.getByText("$2,222")).toHaveCount(0);

  const c2Context = await browser.newContext();
  const c2Page = await c2Context.newPage();
  await attachSession(c2Page, c2Context, c2Token);
  await c2Page.goto("/portal");
  await expect(c2Page.getByText(c2AccountName)).toBeVisible();
  await expect(c2Page.getByText(c1AccountName)).toHaveCount(0);
  await expect(c2Page.getByText("$2,222")).toBeVisible();
  await expect(c2Page.getByText("$1,111")).toHaveCount(0);

  await c1Context.close();
  await c2Context.close();
});
