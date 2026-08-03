import { expect, test } from "@playwright/test";
import {
  destinationForRole,
  isAppRole,
  isPathAllowedForRole,
  safeRelativePath,
} from "../../lib/authRedirect";
import { normalizeProviderConfigs } from "../../lib/providerConfigs";
import { normalizeListPayload } from "../../lib/listPayload";
import {
  normalizeAgencyOverviewPayload,
  normalizeIntegrationsOverviewPayload,
  normalizeOverviewPayload,
} from "../../lib/analyticsPayload";
import { oauthErrorMessage } from "../../lib/oauthError";
import {
  attachSession,
  createAdminSession,
  createAgencySessionWithAccess,
  createClientSessionWithAccess,
} from "./auth";

test("redirect helpers enforce role boundaries and reject external next URLs", () => {
  expect(isAppRole("admin")).toBeTruthy();
  expect(isAppRole("owner")).toBeFalsy();
  expect(safeRelativePath("//example.com/path", "/")).toBe("/");
  expect(safeRelativePath("https://example.com/path", "/")).toBe("/");
  expect(safeRelativePath("/clients?status=active", "/")).toBe("/clients?status=active");

  expect(isPathAllowedForRole("client", "/portal/reports")).toBeTruthy();
  expect(isPathAllowedForRole("client", "/budgets")).toBeFalsy();
  expect(isPathAllowedForRole("agency", "/client/abc")).toBeTruthy();
  expect(isPathAllowedForRole("agency", "/platform/users")).toBeFalsy();
  expect(isPathAllowedForRole("admin", "/platform/settings")).toBeTruthy();
  expect(destinationForRole("client", "/clients")).toBe("/portal");
});

test("list payload parser accepts arrays and envelopes and rejects malformed rows", () => {
  const isNamedItem = (value: unknown): value is { id: string } =>
    Boolean(value) &&
    typeof value === "object" &&
    typeof (value as { id?: unknown }).id === "string";

  expect(normalizeListPayload([{ id: "one" }], isNamedItem, "объектов")).toEqual([{ id: "one" }]);
  expect(normalizeListPayload({ items: [{ id: "two" }], count: 1 }, isNamedItem, "объектов")).toEqual([
    { id: "two" },
  ]);
  expect(() => normalizeListPayload({ items: [{ missing: true }] }, isNamedItem, "объектов")).toThrow(
    "некорректную строку 1",
  );
  expect(() => normalizeListPayload({ items: null }, isNamedItem, "объектов")).toThrow(
    "некорректный список объектов",
  );
});

test("analytics payload parsers reject malformed nested collections before render", () => {
  const overview = normalizeOverviewPayload({
    range: { date_from: "2026-07-01", date_to: "2026-07-31" },
    spend_summary: {},
    budget_summary: {},
    breakdowns: {
      platforms: [{ platform: "meta" }],
      accounts: [{ account_id: "account-1", client_id: "client-1", platform: "meta" }],
    },
  });
  expect(overview.breakdowns.accounts).toHaveLength(1);

  expect(() =>
    normalizeOverviewPayload({
      range: { date_from: "2026-07-01", date_to: "2026-07-31" },
      spend_summary: {},
      budget_summary: {},
      breakdowns: { platforms: [], accounts: null },
    }),
  ).toThrow("некорректный список разбивки по аккаунтам");
  expect(() =>
    normalizeOverviewPayload({
      range: {
        date_from: "2026-07-01",
        date_to: "2026-07-31",
        as_of_date: { unsafe: true },
      },
      spend_summary: {},
      budget_summary: {},
      breakdowns: { platforms: [], accounts: [] },
    }),
  ).toThrow("некорректный обзор показателей");

  expect(normalizeAgencyOverviewPayload({ per_client: [{ client_id: "client-1", spend: 1 }] }).per_client)
    .toHaveLength(1);
  expect(() => normalizeAgencyOverviewPayload({ per_client: "invalid" })).toThrow(
    "некорректный список расходов по клиентам",
  );

  expect(
    normalizeIntegrationsOverviewPayload({ summary: {}, providers: [], events: [] }).providers,
  ).toEqual([]);
  expect(() =>
    normalizeIntegrationsOverviewPayload({ summary: {}, providers: {}, events: [] }),
  ).toThrow("некорректный список подключений");
});

test("provider config parser accepts the API envelope and rejects malformed payloads", () => {
  expect(
    normalizeProviderConfigs({
      items: [
        {
          provider: "facebook",
          enabled: true,
          client_id: "123",
          redirect_uri: "https://example.test/callback",
        },
      ],
      count: 1,
    }),
  ).toEqual([
    {
      provider: "facebook",
      enabled: true,
      client_id: "123",
      redirect_uri: "https://example.test/callback",
      updated_at: undefined,
    },
  ]);
  expect(() => normalizeProviderConfigs({ items: "not-an-array" })).toThrow(
    "некорректный список OAuth-провайдеров",
  );
});

test("OAuth errors are mapped to safe Russian messages", () => {
  expect(oauthErrorMessage("access_denied")).toBe("Вы отменили предоставление доступа.");
  expect(oauthErrorMessage("user_denied")).toBe("Вы отменили предоставление доступа.");
  expect(oauthErrorMessage("access_not_granted")).toBe(
    "Администратор ещё не предоставил вам доступ к платформе. Обратитесь к администратору.",
  );
  expect(oauthErrorMessage("access_pending")).toBe(
    "Ваша учётная запись пока не активна. Дождитесь подтверждения администратора.",
  );
  expect(oauthErrorMessage("account_link_required")).toBe(
    "Вход через Facebook пока не связан с вашей учётной записью. Обратитесь к администратору.",
  );
  expect(oauthErrorMessage("facebook_auth_not_configured")).toBe(
    "Вход через Facebook сейчас недоступен. Используйте другой способ входа или обратитесь к администратору.",
  );
  expect(oauthErrorMessage("provider secret text")).toBe(
    "Не удалось завершить вход через сервис. Попробуйте ещё раз.",
  );
  expect(oauthErrorMessage("")).toBe("");
});

test("Facebook button starts a platform login flow, not an ads connection", async ({ page }) => {
  await page.goto("/login");

  const facebookLogin = page.getByRole("button", { name: "Войти через Facebook" });
  await expect(facebookLogin).toBeVisible();
  await expect(page.getByText(/Только вход в платформу/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Подключить Meta Ads" })).toHaveCount(0);

  await page.route("**/auth/facebook/start?**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "ok" });
  });
  const requestPromise = page.waitForRequest((request) =>
    request.url().includes("/auth/facebook/start?"),
  );
  await facebookLogin.click();
  const oauthRequest = await requestPromise;
  const oauthUrl = new URL(oauthRequest.url());

  expect(oauthUrl.pathname).toMatch(/\/auth\/facebook\/start$/);
  expect(oauthUrl.searchParams.get("intent")).toBe("login");
  expect(oauthUrl.searchParams.get("next")).toBe("/");
});

test("unauthenticated deep link is restored after an agency session appears", async ({ page, context, request }) => {
  await page.goto("/budgets?status=active");
  await expect(page).toHaveURL(/\/login\?/);
  expect(new URL(page.url()).searchParams.get("next")).toBe("/budgets?status=active");

  const token = await createAgencySessionWithAccess(request);
  await attachSession(page, context, token);
  await page.reload();

  await expect(page).toHaveURL(/\/budgets\?status=active$/, { timeout: 15_000 });
});

test("client and agency sessions cannot cross workspace boundaries", async ({ browser, request }) => {
  test.setTimeout(90_000);
  const clientToken = await createClientSessionWithAccess(request);
  const clientContext = await browser.newContext();
  const clientPage = await clientContext.newPage();
  await attachSession(clientPage, clientContext, clientToken);
  await clientPage.goto("/budgets");
  await expect(clientPage).toHaveURL(/\/portal$/, { timeout: 30_000 });
  await clientContext.close();

  const agencyToken = await createAgencySessionWithAccess(request);
  const agencyContext = await browser.newContext();
  const agencyPage = await agencyContext.newPage();
  await attachSession(agencyPage, agencyContext, agencyToken);
  await agencyPage.goto("/portal/reports");
  await expect(agencyPage).toHaveURL(/\/$/, { timeout: 30_000 });
  await agencyContext.close();
});

test("admin settings renders provider config envelopes without crashing", async ({ page, context, request }) => {
  const token = await createAdminSession(request);
  await attachSession(page, context, token);
  await page.goto("/platform/settings");

  await expect(page).toHaveURL(/\/platform\/settings$/, { timeout: 15_000 });
  await expect(page.getByText("Настройки платформы", { exact: true })).toBeVisible();
  await expect(page.getByText("OAuth-провайдеры", { exact: true })).toBeVisible();
});
