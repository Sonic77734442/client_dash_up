import { expect, test } from "@playwright/test";
import { agencySelectionRequiredMessage, resolveAgencySelection } from "../../lib/agencyContext";
import { calculateClientRiskScore } from "../../lib/riskScore";
import type { AgencyOut } from "../../lib/types";
import {
  attachSession,
  createAdminSession,
  createAgencySessionWithAccess,
  createMultiAgencySessionWithAccess,
} from "./auth";

const API_BASE = "http://127.0.0.1:8000";

test("agency context auto-selects one agency and rejects an ambiguous saved choice", () => {
  const north = { id: "north", name: "North" } as AgencyOut;
  const south = { id: "south", name: "South" } as AgencyOut;

  expect(resolveAgencySelection([north], null)).toBe("north");
  expect(resolveAgencySelection([north, south], "south")).toBe("south");
  expect(resolveAgencySelection([north, south], "removed")).toBe("");
  expect(agencySelectionRequiredMessage()).toContain("Выберите текущее агентство");
  expect(calculateClientRiskScore(60, 5_000_000, 5_000_000, false)).toBe(
    calculateClientRiskScore(60, 1_000, 5_000_000, false),
  );
});

test("agency workspace routes are stable and role-scoped", async ({ page, context, request }) => {
  test.setTimeout(90_000);
  const token = await createAgencySessionWithAccess(request);
  const agenciesResponse = await request.get(`${API_BASE}/platform/agencies?status=active`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(agenciesResponse.ok()).toBeTruthy();
  const agencyRows = (await agenciesResponse.json()) as { items: AgencyOut[] };
  const agency = agencyRows.items[0];
  expect(agency?.id).toBeTruthy();
  await attachSession(page, context, token);

  await page.goto("/");
  await expect(page.getByRole("link", { name: "Центр решений" })).toHaveCount(0);
  await expect(page.locator(".topbar-title").first()).toBeVisible();

  await expect(page.getByRole("link", { name: "Клиенты" }).first()).toBeVisible();

  const routes = ["/integrations", "/sync-monitor", "/budgets"];

  for (const path of routes) {
    await page.goto(path);
    await expect(page).toHaveURL(new RegExp(`${path.replace("/", "\\/")}$`));
    await expect(page.locator(".topbar-title").first()).toHaveText(/\S+/, { timeout: 30_000 });
  }

  await page.goto("/sync-monitor");
  await expect(page.locator(".agency-context-current")).toBeVisible({ timeout: 30_000 });

  await page.route("**/ad-accounts/discover", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_provider: "all",
        client_id: "00000000-0000-0000-0000-000000000001",
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
  const discoverRequestPromise = page.waitForRequest((candidate) => (
    candidate.method() === "POST" && candidate.url().endsWith("/ad-accounts/discover")
  ));
  const discoverButton = page.getByRole("button", { name: "Найти аккаунты", exact: true }).first();
  await expect(discoverButton).toBeEnabled({ timeout: 30_000 });
  await discoverButton.click();
  const discoverRequest = await discoverRequestPromise;
  expect(discoverRequest.postDataJSON()).toMatchObject({ agency_id: agency.id });

  const metaConnect = page.getByRole("button", { name: "Подключить Meta Ads" }).first();
  await expect(metaConnect).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Войти через Facebook" })).toHaveCount(0);
  await metaConnect.click();
  await expect(page.getByRole("heading", { name: "Подключить Meta Ads" })).toBeVisible();

  await page.route("**/api/connect/start?**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "ok" });
  });
  const requestPromise = page.waitForRequest((oauthRequest) =>
    oauthRequest.url().includes("/api/connect/start?"),
  );
  await page.getByRole("button", { name: "Перейти к авторизации" }).click();
  const oauthRequest = await requestPromise;
  const oauthUrl = new URL(oauthRequest.url());

  expect(oauthUrl.pathname).toBe("/api/connect/start");
  expect(oauthUrl.searchParams.get("source")).toBe("m");
  expect(oauthUrl.searchParams.get("intent")).toBe("connect");
  expect(oauthUrl.searchParams.get("connect_mode")).toBe("add");
  expect(oauthUrl.searchParams.get("agency_id")).toBe(agency.id);
});

test("archived clients are not offered as discovery targets", async ({ page, context, request }) => {
  const token = await createAdminSession(request);
  const headers = { Authorization: `Bearer ${token}` };
  const activeName = `active-discovery-${Date.now()}`;
  const archivedName = `archived-discovery-${Date.now()}`;

  const activeResponse = await request.post(`${API_BASE}/clients`, {
    headers,
    data: { name: activeName, status: "active", default_currency: "USD" },
  });
  expect(activeResponse.ok()).toBeTruthy();

  const archivedResponse = await request.post(`${API_BASE}/clients`, {
    headers,
    data: { name: archivedName, status: "active", default_currency: "USD" },
  });
  expect(archivedResponse.ok()).toBeTruthy();
  const archivedClient = (await archivedResponse.json()) as { id: string };
  const archiveResponse = await request.delete(`${API_BASE}/clients/${archivedClient.id}`, { headers });
  expect(archiveResponse.ok()).toBeTruthy();

  await attachSession(page, context, token);
  await page.goto("/sync-monitor");

  const discoveryTarget = page.getByLabel("Клиент для найденных аккаунтов");
  await expect(discoveryTarget).toBeVisible({ timeout: 30_000 });
  await expect(discoveryTarget.locator("option", { hasText: activeName })).toHaveCount(1);
  await expect(discoveryTarget.locator("option", { hasText: archivedName })).toHaveCount(0);
});

test("client registry stays fail-closed when agency scope cannot be loaded", async ({ page, context, request }) => {
  const token = await createAgencySessionWithAccess(request);
  await attachSession(page, context, token);
  let clientListRequests = 0;

  await page.route("**/platform/agencies?status=active", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "agency_scope_unavailable", message: "Не удалось загрузить агентства для проверки доступа." },
      }),
    });
  });
  await page.route("**/clients?status=all", async (route) => {
    clientListRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [{ id: "foreign-client", name: "Чужой клиент", status: "active" }] }),
    });
  });

  await page.goto("/clients");
  const agencySelect = page.getByLabel("Текущее агентство");
  await expect(agencySelect).toBeVisible({ timeout: 30_000 });
  await expect(agencySelect).toBeDisabled();
  await expect(
    page.locator("main .warning").filter({ hasText: "Не удалось загрузить агентства для проверки доступа." }),
  ).toBeVisible();
  await page.waitForTimeout(300);
  expect(clientListRequests).toBe(0);
  await expect(page.getByText("Чужой клиент", { exact: true })).toHaveCount(0);
});

test("switching agency drops delayed old scope and constrains bulk sync", async ({ page, context, request }) => {
  test.setTimeout(120_000);
  const fixture = await createMultiAgencySessionWithAccess(request);
  const [north, south] = fixture.fixtures;
  await attachSession(page, context, fixture.token);

  await page.goto("/clients");
  const agencySelect = page.getByLabel("Текущее агентство");
  await expect(agencySelect).toBeVisible({ timeout: 30_000 });
  await expect(agencySelect).toHaveValue("");

  await page.route(`**/platform/agencies/${north.agencyId}/clients`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    await route.continue();
  });
  const delayedNorth = page.waitForRequest((candidate) => (
    candidate.url().includes(`/platform/agencies/${north.agencyId}/clients`)
  ));
  await agencySelect.selectOption(north.agencyId);
  await delayedNorth;
  await agencySelect.selectOption(south.agencyId);

  await expect(agencySelect).toHaveValue(south.agencyId);
  await expect(page.getByText(south.clientId.slice(0, 8), { exact: false }).first()).toBeVisible({ timeout: 30_000 });
  await new Promise((resolve) => setTimeout(resolve, 1_200));
  await expect(page.getByText(north.clientId.slice(0, 8), { exact: false })).toHaveCount(0);

  let reloadAuthRequests = 0;
  page.on("request", (networkRequest) => {
    if (new URL(networkRequest.url()).pathname.endsWith("/auth/me")) reloadAuthRequests += 1;
  });
  await page.reload();
  await expect(page.getByLabel("Текущее агентство")).toHaveValue(south.agencyId, { timeout: 30_000 });
  await page.waitForTimeout(300);
  expect(reloadAuthRequests).toBe(1);

  await page.goto("/accounts");
  const accountsAgencySelect = page.getByLabel("Текущее агентство");
  await expect(accountsAgencySelect).toHaveValue(south.agencyId, { timeout: 30_000 });
  await accountsAgencySelect.selectOption(north.agencyId);
  const northRow = page.getByRole("row").filter({ hasText: north.accountId.slice(0, 8) });
  await expect(northRow).toBeVisible({ timeout: 30_000 });
  await northRow.getByRole("checkbox").check();
  await expect(page.getByTestId("bulk-assign-client")).toBeEnabled();

  await accountsAgencySelect.selectOption(south.agencyId);
  await expect(page.getByTestId("bulk-assign-client")).toBeDisabled();
  await expect(page.getByRole("row").filter({ hasText: south.accountId.slice(0, 8) })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("row").filter({ hasText: north.accountId.slice(0, 8) })).toHaveCount(0);

  await page.route("**/ad-accounts/sync/run", async (route) => {
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
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        jobs: [],
      }),
    });
  });
  const syncRequestPromise = page.waitForRequest((candidate) => (
    candidate.method() === "POST" && candidate.url().endsWith("/ad-accounts/sync/run")
  ));
  await page.getByRole("button", { name: "Обновить данные" }).click();
  const syncRequest = await syncRequestPromise;
  expect(syncRequest.postDataJSON()).toMatchObject({ account_ids: [south.accountId] });
});
