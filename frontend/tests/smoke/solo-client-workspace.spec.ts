import { expect, test } from "@playwright/test";
import { attachSession, createSoloClientSessionWithAccess } from "./auth";

test("solo owner sees only their portal and data-management workspace", async ({ page, context, request }) => {
  test.setTimeout(90_000);
  const fixture = await createSoloClientSessionWithAccess(request);
  await attachSession(page, context, fixture.token);

  await page.goto("/portal");
  await expect(page).toHaveURL(/\/portal$/);
  await expect(page.getByText(/самостоятельный кабинет/i).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Источники рекламы" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Мои бюджеты" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Клиенты" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Команда и доступы" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Агентства" })).toHaveCount(0);

  await page.goto("/clients");
  await expect(page).toHaveURL(/\/portal$/, { timeout: 15_000 });

  for (const forbiddenPath of [`/client/${fixture.clientId}`, "/agency/team", "/platform/users"]) {
    await page.goto(forbiddenPath);
    await expect(page).toHaveURL(/\/portal$/, { timeout: 15_000 });
  }

  await page.goto("/integrations");
  await expect(page).toHaveURL(/\/integrations$/);
  await expect(page.getByText("Источники рекламы", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Текущее агентство", { exact: true })).toHaveCount(0);
});

test("solo owner connect, discovery and sync requests carry the sole client scope", async ({ page, context, request }) => {
  const fixture = await createSoloClientSessionWithAccess(request);
  await attachSession(page, context, fixture.token);
  await page.goto("/sync-monitor");
  await expect(page).toHaveURL(/\/sync-monitor$/);
  await expect(
    page.locator("#provider-connections").getByText(fixture.clientName, { exact: true }),
  ).toBeVisible({ timeout: 15_000 });

  let discoveryPayload: Record<string, unknown> | null = null;
  await page.route("**/api/backend/ad-accounts/discover", async (route) => {
    discoveryPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_provider: "all",
        client_id: fixture.clientId,
        discovered: 0,
        created: 0,
        updated: 0,
        skipped: 0,
        providers_attempted: [],
        providers_failed: {},
        items: [],
      }),
    });
  });
  await page.locator(".data-connection-actions").getByRole("button", { name: "Найти аккаунты", exact: true }).click();
  await expect.poll(() => discoveryPayload).not.toBeNull();
  expect(discoveryPayload).toMatchObject({ client_id: fixture.clientId, upsert_existing: true });
  expect(discoveryPayload).not.toHaveProperty("agency_id");

  let syncPayload: Record<string, unknown> | null = null;
  await page.route("**/api/backend/ad-accounts/sync/run", async (route) => {
    syncPayload = route.request().postDataJSON() as Record<string, unknown>;
    const now = new Date().toISOString();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested: 1,
        processed: 1,
        skipped: 0,
        success: 1,
        failed: 0,
        retry_scheduled: 0,
        started_at: now,
        finished_at: now,
        jobs: [],
      }),
    });
  });
  await page.getByRole("button", { name: "Обновить данные за 30 дней" }).click();
  await expect.poll(() => syncPayload).not.toBeNull();
  expect(syncPayload).toMatchObject({ client_id: fixture.clientId, account_ids: [fixture.accountId] });

  await page.route("**/api/connect/start?**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "ok" });
  });
  await page.getByRole("button", { name: "Подключить Google Ads", exact: true }).first().click();
  const oauthRequestPromise = page.waitForRequest((candidate) => candidate.url().includes("/api/connect/start?"));
  await page.getByRole("button", { name: "Перейти к авторизации" }).click();
  const oauthUrl = new URL((await oauthRequestPromise).url());
  expect(oauthUrl.pathname).toBe("/api/connect/start");
  expect(oauthUrl.searchParams.get("source")).toBe("g");
  expect(oauthUrl.searchParams.get("intent")).toBe("connect");
  expect(oauthUrl.searchParams.get("client_id")).toBe(fixture.clientId);
  expect(oauthUrl.searchParams.has("agency_id")).toBeFalsy();
});

for (const accessibleClientIds of [[], ["client-one", "client-two"]]) {
  test(`solo owner fails closed with ${accessibleClientIds.length} assigned clients`, async ({ page }) => {
    let tenantDataRequests = 0;
    await page.route("**/api/backend/**", async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.endsWith("/auth/me")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              id: "solo-user",
              email: "solo@test.local",
              name: "Solo owner",
              role: "solo_client",
              status: "active",
            },
            session: {
              valid: true,
              user_id: "solo-user",
              role: "solo_client",
              global_access: false,
              access_scope: "assigned",
              accessible_client_ids: accessibleClientIds,
            },
          }),
        });
        return;
      }
      tenantDataRequests += 1;
      await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
    });

    await page.goto("/portal");
    await expect(page.getByText("Для самостоятельного кабинета должен быть назначен ровно один активный клиент.")).toBeVisible();
    expect(tenantDataRequests).toBe(0);
  });
}
