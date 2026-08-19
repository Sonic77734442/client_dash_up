import { expect, test, type Page, type Route } from "@playwright/test";

const CLIENT_ID = "11111111-1111-4111-8111-111111111111";
const ACCOUNT_ID = "22222222-2222-4222-8222-222222222222";
const USER_ID = "33333333-3333-4333-8333-333333333333";
const AGENCY_ID = "66666666-6666-4666-8666-666666666666";
const NOW = "2026-08-18T10:00:00Z";

test.describe.configure({ timeout: 60_000 });

type Role = "solo_client" | "client" | "agency";

function authMe(role: Role) {
  return {
    user: {
      id: USER_ID,
      email: `${role}@test.local`,
      name: role === "client" ? "Client viewer" : role === "agency" ? "Agency member" : "Solo owner",
      role,
      status: "active",
    },
    session: {
      valid: true,
      user_id: USER_ID,
      role,
      global_access: false,
      access_scope: "assigned",
      accessible_client_ids: [CLIENT_ID],
    },
  };
}

const client = { id: CLIENT_ID, name: "Acme", status: "active", default_currency: "USD" };
const account = {
  id: ACCOUNT_ID,
  client_id: CLIENT_ID,
  platform: "meta",
  external_account_id: "act_123456789",
  name: "Acme Meta",
  currency: "USD",
  status: "active",
};

const targetResponse = {
  provider: "meta",
  account_id: ACCOUNT_ID,
  count: 1,
  items: [
    {
      target_type: "campaign",
      provider_target_id: "987654321",
      name: "Leads · Kazakhstan",
      status: "ACTIVE",
      budget_fields: [
        {
          field: "daily_budget",
          current_minor: 10_000,
          currency: "USD",
          observed_at: NOW,
          editable: true,
        },
      ],
    },
  ],
};

function overview() {
  return {
    range: { date_from: "2026-08-01", date_to: "2026-08-18", as_of_date: "2026-08-18", timezone_policy: "UTC" },
    scope: { client_id: CLIENT_ID, account_id: null },
    spend_summary: { spend: 0, impressions: 0, clicks: 0, conversions: 0, ctr: 0, cpc: 0, cpm: 0 },
    budget_summary: {
      budget: null,
      spend: 0,
      remaining: null,
      usage_percent: null,
      expected_spend_to_date: null,
      forecast_spend: null,
      pace_status: "no_budget",
      pace_delta: null,
      pace_delta_percent: null,
    },
    breakdowns: { platforms: [], accounts: [] },
    data_quality: {
      status: "insufficient_data",
      rows_present: false,
      row_count: 0,
      latest_data_date: null,
      stale_days: null,
      stale_after_days: 3,
      active_accounts_count: 1,
      accounts_with_data_count: 0,
      accounts_without_data_count: 1,
      coverage_percent: 0,
    },
  };
}

async function installBaseApi(
  page: Page,
  role: Role,
  providerHandler: (route: Route, path: string) => Promise<void>,
  agencyMemberRole: "owner" | "manager" | "member" = "owner",
  accountRows: Array<typeof account> = [account],
  clientRows: Array<typeof client> = [client],
) {
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api\/backend/, "");
    if (path.startsWith("/provider-controls/meta/")) {
      await providerHandler(route, path);
      return;
    }
    if (path === "/auth/me") {
      const auth = authMe(role);
      auth.session.accessible_client_ids = clientRows.map((item) => item.id);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(auth) });
      return;
    }
    if (path === "/auth/csrf") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ csrf_token: "smoke-csrf" }) });
      return;
    }
    if (path === "/platform/agencies" && role === "agency") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{
            id: AGENCY_ID,
            name: "North Agency",
            slug: "north-agency",
            status: "active",
            plan: "starter",
            allow_client_invites: true,
            created_at: NOW,
            updated_at: NOW,
          }],
        }),
      });
      return;
    }
    if (path === `/platform/agencies/${AGENCY_ID}/clients` && role === "agency") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(clientRows.map((item, index) => ({
          id: `77777777-7777-4777-8777-${String(index + 1).padStart(12, "0")}`,
          agency_id: AGENCY_ID,
          client_id: item.id,
          created_at: NOW,
          updated_at: NOW,
        }))),
      });
      return;
    }
    if (path === `/platform/agencies/${AGENCY_ID}/members` && role === "agency") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{
          id: "88888888-8888-4888-8888-888888888888",
          agency_id: AGENCY_ID,
          user_id: USER_ID,
          role: agencyMemberRole,
          status: "active",
          created_at: NOW,
          updated_at: NOW,
        }]),
      });
      return;
    }
    if (path === "/clients") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: clientRows }) });
      return;
    }
    if (path === "/ad-accounts") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: accountRows }) });
      return;
    }
    if (path === "/budgets" || path === "/ad-stats" || path === "/insights/operational/actions" || path === "/insights/operational") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
      return;
    }
    if (path === "/insights/overview") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(overview()) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "not_found", message: path } }) });
  });
}

function readiness(overrides: Record<string, unknown> = {}) {
  return {
    provider: "meta",
    feature_enabled: true,
    visible: true,
    can_read_history: true,
    can_preview: true,
    can_confirm: true,
    can_reconcile: true,
    credential_ready: true,
    binding_ready: true,
    role: "solo_client",
    account: { id: ACCOUNT_ID, name: "Acme Meta", provider_account_id: "123456789", currency: "USD" },
    allowed: {
      target_types: ["campaign", "ad_set"],
      fields_by_target: { campaign: ["daily_budget", "lifetime_budget"], ad_set: ["daily_budget", "lifetime_budget"] },
    },
    ...overrides,
  };
}

test("provider budget controls stay hidden when backend does not expose the capability", async ({ page }) => {
  await installBaseApi(page, "solo_client", async (route) => {
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/budgets");
  await expect(page.getByText("Плановые бюджеты", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Бюджеты Meta" })).toHaveCount(0);
});

test("missing ads_management is explained without exposing a mutation action", async ({ page }) => {
  let targetRequests = 0;
  await installBaseApi(page, "solo_client", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(readiness({
          can_preview: false,
          can_confirm: false,
          credential_ready: false,
          binding_ready: true,
          reason_code: "meta_permissions_missing",
          message: "Подключите Meta заново и разрешите ads_management.",
        })),
      });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], count: 0 }) });
      return;
    }
    if (path.includes("/budget-targets")) targetRequests += 1;
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/budgets");
  await expect(page.getByRole("heading", { name: "Бюджеты Meta" })).toBeVisible();
  await expect(page.getByText("Переподключите Meta и разрешите управление рекламными бюджетами.")).toBeVisible();
  await expect(page.getByText(/Для поддержки: требуется разрешение ads.management\./)).toBeVisible();
  await expect(page.getByRole("link", { name: "Проверить подключение Meta" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Проверить изменение" })).toHaveCount(0);
  expect(targetRequests).toBe(0);
});

test("feature-off state keeps history visible without a false reconnect prompt", async ({ page }) => {
  let targetRequests = 0;
  const historyCommand = {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    status: "applied",
    request: {
      client_id: CLIENT_ID,
      ad_account_id: ACCOUNT_ID,
      target_type: "campaign",
      provider_target_id: "987654321",
      field: "daily_budget",
      amount_minor: 11_000,
      currency: "USD",
      expected_current_minor: 10_000,
      reason: "Сохранённая история при выключенной функции",
    },
    observed_before_minor: 10_000,
    confirmed_after_minor: 11_000,
    attempt_count: 1,
    error: null,
    attempts: [],
    created_at: NOW,
    updated_at: NOW,
    completed_at: NOW,
  };
  await installBaseApi(page, "solo_client", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(readiness({
          feature_enabled: false,
          can_preview: false,
          can_confirm: false,
          can_reconcile: false,
          credential_ready: false,
          binding_ready: false,
          reason_code: "meta_budget_controls_disabled",
          message: "Meta budget controls are not enabled.",
        })),
      });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [historyCommand], count: 1 }) });
      return;
    }
    if (path.includes("/budget-targets")) targetRequests += 1;
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/budgets");
  await expect(page.getByText("Управление бюджетами Meta пока выключено для этого контура.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Изменения бюджетов Meta" })).toBeVisible();
  await expect(page.getByText("Сохранённая история при выключенной функции", { exact: true })).toBeVisible();
  await expect(page.getByText(/Для поддержки: требуется разрешение ads.management./)).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Проверить подключение Meta" })).toHaveCount(0);
  expect(targetRequests).toBe(0);
});

test("solo owner can safely reconcile an unknown command while new Meta writes are disabled", async ({ page }) => {
  let targetRequests = 0;
  let reconcileRequests = 0;
  const unknownCommand = {
    id: "12121212-1212-4212-8212-121212121212",
    status: "unknown",
    request: {
      client_id: CLIENT_ID,
      ad_account_id: ACCOUNT_ID,
      target_type: "campaign",
      provider_target_id: "987654321",
      field: "daily_budget",
      amount_minor: 11_000,
      currency: "USD",
      expected_current_minor: 10_000,
      reason: "Проверяем зависшую команду после остановки новых изменений",
    },
    observed_before_minor: 10_000,
    confirmed_after_minor: null,
    attempt_count: 1,
    error: { code: "provider_write_unknown", message: "Meta не подтвердила результат", retryable: false },
    attempts: [],
    created_at: NOW,
    updated_at: NOW,
    completed_at: null,
  };

  await installBaseApi(page, "solo_client", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(readiness({
          feature_enabled: false,
          can_preview: false,
          can_confirm: false,
          can_reconcile: true,
          credential_ready: true,
          binding_ready: true,
          reason_code: "meta_budget_controls_disabled",
          message: "New Meta budget writes are disabled.",
        })),
      });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [unknownCommand], count: 1 }),
      });
      return;
    }
    if (path === `/provider-controls/meta/budget-changes/${unknownCommand.id}/reconcile`) {
      reconcileRequests += 1;
      expect(route.request().method()).toBe("POST");
      expect(route.request().postData()).toBeNull();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...unknownCommand,
          status: "applied",
          confirmed_after_minor: 11_000,
          attempt_count: 2,
          attempts: [{
            attempt_no: 2,
            outcome: "applied",
            started_at: NOW,
            finished_at: NOW,
            observed_before_minor: 10_000,
            confirmed_after_minor: 11_000,
            reconciliation: true,
          }],
          updated_at: NOW,
          completed_at: NOW,
        }),
      });
      return;
    }
    if (path.includes("/budget-targets")) targetRequests += 1;
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/budgets");
  await expect(page.getByText("Управление бюджетами Meta пока выключено для этого контура.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Проверить изменение" })).toHaveCount(0);
  await page.getByRole("button", { name: /Детали изменения.*Статус неизвестен/ }).click();
  await page.getByRole("button", { name: "Проверить статус" }).click();
  await expect(page.getByText("Применено", { exact: true }).first()).toBeVisible();
  expect(reconcileRequests).toBe(1);
  expect(targetRequests).toBe(0);
});

test("solo owner previews and confirms one allowlisted Meta budget change", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let previewPayload: Record<string, unknown> | null = null;
  let confirmPayload: Record<string, unknown> | null = null;
  let idempotencyKey = "";
  let commandCreated = false;
  let confirmRequests = 0;
  let reconcileRequests = 0;
  let resolveRequests = 0;
  let resolvePayload: Record<string, unknown> | null = null;

  const requestPayload = {
    client_id: CLIENT_ID,
    ad_account_id: ACCOUNT_ID,
    target_type: "campaign",
    provider_target_id: "987654321",
    field: "daily_budget",
    amount_minor: 15_000,
    currency: "USD",
    expected_current_minor: 10_000,
    reason: "Усиливаем кампанию с лучшей стоимостью лида",
  };
  const command = {
    id: "44444444-4444-4444-8444-444444444444",
    status: "unknown",
    request: requestPayload,
    observed_before_minor: 10_000,
    confirmed_after_minor: null,
    attempt_count: 1,
    error: { code: "provider_write_unknown", message: "Meta не подтвердила результат", retryable: false },
    attempts: [],
    created_at: NOW,
    updated_at: NOW,
    completed_at: null,
  };

  await installBaseApi(page, "solo_client", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(readiness()) });
      return;
    }
    if (path.endsWith(`/accounts/${ACCOUNT_ID}/budget-targets`)) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(targetResponse) });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes/preview") {
      previewPayload = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          preview_token: "p".repeat(64),
          issued_at: NOW,
          expires_at: "2099-08-18T10:05:00Z",
          current_minor: 10_000,
          requested_minor: 15_000,
          delta_minor: 5_000,
          currency: "USD",
          request: requestPayload,
          warnings: [{ code: "large_delta", message: "Изменение составляет 50%.", severity: "warning" }],
        }),
      });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes" && route.request().method() === "POST") {
      commandCreated = true;
      confirmRequests += 1;
      confirmPayload = route.request().postDataJSON() as Record<string, unknown>;
      idempotencyKey = route.request().headers()["idempotency-key"] || "";
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ command, replayed: false }) });
      return;
    }
    if (path === `/provider-controls/meta/budget-changes/${command.id}/reconcile`) {
      reconcileRequests += 1;
      expect(route.request().method()).toBe("POST");
      expect(route.request().postData()).toBeNull();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...command,
          attempt_count: 2,
          attempts: [{
            attempt_no: 2,
            outcome: "unknown",
            started_at: "2026-08-18T10:01:00Z",
            finished_at: "2026-08-18T10:01:01Z",
            observed_before_minor: 10_000,
            confirmed_after_minor: null,
            reconciliation: true,
          }],
          updated_at: "2026-08-18T10:01:00Z",
        }),
      });
      return;
    }
    if (path === `/provider-controls/meta/budget-changes/${command.id}/resolve-unknown`) {
      resolveRequests += 1;
      resolvePayload = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...command,
          status: "conflict",
          error: { code: "provider_reconciliation_mismatch", message: "Зафиксировано текущее значение Meta", retryable: false },
          attempt_count: 3,
          attempts: [{
            attempt_no: 3,
            outcome: "conflict",
            started_at: "2026-08-18T10:02:00Z",
            finished_at: "2026-08-18T10:02:01Z",
            observed_before_minor: 10_000,
            confirmed_after_minor: 10_000,
            reconciliation: true,
          }],
          updated_at: "2026-08-18T10:02:01Z",
          completed_at: "2026-08-18T10:02:01Z",
        }),
      });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes" && route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: commandCreated ? [command] : [], count: commandCreated ? 1 : 0 }),
      });
      return;
    }
    if (path === `/provider-controls/meta/budget-changes/${command.id}`) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(command) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/budgets");
  await expect(page.getByRole("heading", { name: "Бюджеты Meta" })).toBeVisible();
  await expect(page.getByText(/100,00/).first()).toBeVisible();

  await expect(page.getByLabel("Причина изменения бюджета Meta")).toHaveAttribute("required", "");
  await expect(page.getByLabel("Причина изменения бюджета Meta")).toHaveAttribute("aria-required", "true");
  await page.getByLabel("Новая сумма бюджета Meta").fill("150");
  await page.getByLabel("Причина изменения бюджета Meta").fill(requestPayload.reason);
  await page.getByRole("button", { name: "Проверить изменение" }).click();

  await expect(page.getByText("Что изменится в Meta", { exact: true })).toBeVisible();
  await expect(page.getByText("Изменение составляет 50%.")).toBeVisible();
  expect(previewPayload).toEqual(requestPayload);
  expect(previewPayload).not.toHaveProperty("credential_id");
  expect(previewPayload).not.toHaveProperty("provider_account_id");
  expect(previewPayload).not.toHaveProperty("spend_cap");

  await expect(page.getByRole("button", { name: "Подтвердить и отправить в Meta" })).toBeDisabled();
  expect(confirmPayload).toBeNull();
  await page.getByLabel(/Я проверил\(а\) клиента/).check();
  await page.getByRole("button", { name: "Подтвердить и отправить в Meta" }).click();

  await expect(page.getByText("Статус неизвестен").first()).toBeVisible();
  await expect(page.getByText("Не повторяйте команду", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Команда уже отправлена" })).toBeDisabled();
  expect(confirmPayload).toMatchObject({ ...requestPayload, preview_token: "p".repeat(64), confirm: true });
  expect(idempotencyKey).toMatch(/^meta-budget-[A-Za-z0-9-]+$/);
  expect(confirmRequests).toBe(1);
  await page.getByRole("button", { name: "Проверить статус" }).click();
  await expect(page.getByText("Сверка не смогла подтвердить прежнюю команду", { exact: true })).toBeVisible();
  expect(reconcileRequests).toBe(1);
  expect(confirmRequests).toBe(1);
  await expect(page.getByRole("button", { name: "Принять текущее состояние Meta" })).toBeDisabled();
  expect(resolvePayload).toBeNull();
  await page.getByLabel(/Я проверил\(а\) текущее состояние в Meta/).check();
  await page.getByRole("button", { name: "Принять текущее состояние Meta" }).click();
  await expect(page.getByText("Конфликт", { exact: true }).first()).toBeVisible();
  expect(resolvePayload).toEqual({ confirm: true, resolution: "accept_current_state" });
  expect(resolveRequests).toBe(1);
  expect(confirmRequests).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});

test("client sees live Meta values and conflict history without write controls", async ({ page }) => {
  const conflictCommand = {
    id: "55555555-5555-4555-8555-555555555555",
    status: "conflict",
    request: {
      client_id: CLIENT_ID,
      ad_account_id: ACCOUNT_ID,
      target_type: "campaign",
      provider_target_id: "987654321",
      field: "daily_budget",
      amount_minor: 12_000,
      currency: "USD",
      expected_current_minor: 10_000,
      reason: "Согласованная корректировка",
    },
    observed_before_minor: 10_000,
    confirmed_after_minor: null,
    attempt_count: 0,
    error: { code: "provider_value_conflict", message: "Текущее значение уже изменилось", retryable: false },
    attempts: [],
    created_at: NOW,
    updated_at: NOW,
    completed_at: NOW,
  };
  const unknownCommand = {
    ...conflictCommand,
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    status: "unknown",
    error: { code: "provider_write_unknown", message: "Результат записи не подтверждён", retryable: false },
    attempts: [{
      attempt_no: 1,
      outcome: "unknown",
      started_at: NOW,
      finished_at: NOW,
      observed_before_minor: 10_000,
      confirmed_after_minor: null,
      reconciliation: true,
    }],
    completed_at: null,
  };

  await installBaseApi(page, "client", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(readiness({ role: "client", can_preview: false, can_confirm: false, can_reconcile: false })),
      });
      return;
    }
    if (path.endsWith(`/accounts/${ACCOUNT_ID}/budget-targets`)) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(targetResponse) });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [conflictCommand, unknownCommand], count: 2 }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/portal/billing");
  await expect(page.getByRole("heading", { name: "Бюджеты Meta" })).toBeVisible();
  await expect(page.getByText("Ваша роль может видеть фактические значения", { exact: false })).toBeVisible();
  await expect(page.getByText("Конфликт", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Проверить изменение" })).toHaveCount(0);
  await expect(page.getByLabel("Новая сумма бюджета Meta")).toHaveCount(0);
  await page.getByRole("button", { name: /Детали изменения.*Статус неизвестен/ }).click();
  await expect(page.getByText("Сверка с Meta доступна только владельцу или менеджеру", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Проверить статус" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Принять текущее состояние Meta" })).toHaveCount(0);
});

test("history remains available when the client has no active Meta account", async ({ page }) => {
  let readinessRequests = 0;
  const archivedCommand = {
    id: "99999999-9999-4999-8999-999999999999",
    status: "conflict",
    request: {
      client_id: CLIENT_ID,
      ad_account_id: ACCOUNT_ID,
      target_type: "campaign",
      provider_target_id: "987654321",
      field: "daily_budget",
      amount_minor: 12_000,
      currency: "USD",
      expected_current_minor: 10_000,
      reason: "Архивная корректировка бюджета",
    },
    observed_before_minor: 10_000,
    confirmed_after_minor: null,
    attempt_count: 0,
    error: { code: "provider_value_conflict", message: "Значение было изменено в Meta", retryable: false },
    attempts: [],
    created_at: NOW,
    updated_at: NOW,
    completed_at: NOW,
  };

  await installBaseApi(page, "client", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      readinessRequests += 1;
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      const url = new URL(route.request().url());
      expect(url.searchParams.get("client_id")).toBe(CLIENT_ID);
      expect(url.searchParams.has("ad_account_id")).toBe(false);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [archivedCommand], count: 1 }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  }, "owner", []);

  await page.goto("/portal/billing");
  await expect(page.getByRole("heading", { name: "Бюджеты Meta" })).toBeVisible();
  await expect(page.getByText("История доступна", { exact: true })).toBeVisible();
  const accountSelect = page.getByLabel("Рекламный аккаунт Meta");
  await expect(accountSelect).toHaveValue("");
  await expect(accountSelect.locator("option:checked")).toHaveText("Нет активных аккаунтов Meta");
  await expect(page.getByText("Активный рекламный аккаунт Meta недоступен", { exact: true })).toBeVisible();
  await expect(page.getByText("Архивная корректировка бюджета", { exact: true })).toBeVisible();
  await expect(page.getByText("Конфликт", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Новая сумма бюджета Meta")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Подтвердить и отправить в Meta" })).toHaveCount(0);
  expect(readinessRequests).toBe(0);
});

test("history loading error remains visible without readiness or an active Meta account", async ({ page }) => {
  await installBaseApi(page, "client", async (route, path) => {
    if (path === "/provider-controls/meta/budget-changes") {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "history_temporarily_unavailable",
            message: "История Meta временно недоступна. Повторите обновление позже.",
          },
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  }, "owner", []);

  await page.goto("/portal/billing");
  await expect(page.getByRole("heading", { name: "Бюджеты Meta" })).toBeVisible();
  await expect(page.getByText("История недоступна", { exact: true })).toBeVisible();
  await expect(page.getByText("История Meta временно недоступна. Повторите обновление позже.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Бюджеты Meta").getByRole("button", { name: "Обновить" })).toBeVisible();
});

test("a slower previous client request cannot replace the selected client history", async ({ page }) => {
  const secondClient = {
    id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    name: "Beta",
    status: "active",
    default_currency: "USD",
  };
  const secondAccount = {
    ...account,
    id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    client_id: secondClient.id,
    external_account_id: "act_987654321",
    name: "Beta Meta",
  };
  const historyCommand = (id: string, clientId: string, accountId: string, reason: string) => ({
    id,
    status: "applied",
    request: {
      client_id: clientId,
      ad_account_id: accountId,
      target_type: "campaign",
      provider_target_id: "987654321",
      field: "daily_budget",
      amount_minor: 11_000,
      currency: "USD",
      expected_current_minor: 10_000,
      reason,
    },
    observed_before_minor: 10_000,
    confirmed_after_minor: 11_000,
    attempt_count: 1,
    error: null,
    attempts: [],
    created_at: NOW,
    updated_at: NOW,
    completed_at: NOW,
  });
  let releaseFirst: (() => void) | undefined;
  let markFirstStarted: (() => void) | undefined;
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const firstStarted = new Promise<void>((resolve) => { markFirstStarted = resolve; });

  await installBaseApi(page, "agency", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      const accountId = new URL(route.request().url()).searchParams.get("ad_account_id") || ACCOUNT_ID;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(readiness({
          role: "agency",
          feature_enabled: false,
          can_preview: false,
          can_confirm: false,
          credential_ready: false,
          binding_ready: false,
          reason_code: "meta_budget_controls_disabled",
          account: { id: accountId, name: accountId === ACCOUNT_ID ? "Acme Meta" : "Beta Meta", currency: "USD" },
        })),
      });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      const clientId = new URL(route.request().url()).searchParams.get("client_id");
      if (clientId === CLIENT_ID) {
        markFirstStarted?.();
        await firstGate;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: [historyCommand("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", CLIENT_ID, ACCOUNT_ID, "Старая история Acme")],
            count: 1,
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [historyCommand("ffffffff-ffff-4fff-8fff-ffffffffffff", secondClient.id, secondAccount.id, "Актуальная история Beta")],
          count: 1,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  }, "owner", [account, secondAccount], [client, secondClient]);

  await page.goto("/budgets");
  await firstStarted;
  await page.getByLabel("Клиент для бюджета Meta").selectOption(secondClient.id);
  await expect(page.getByText("Актуальная история Beta", { exact: true })).toBeVisible();
  releaseFirst?.();
  await page.waitForTimeout(200);
  await expect(page.getByText("Актуальная история Beta", { exact: true })).toBeVisible();
  await expect(page.getByText("Старая история Acme", { exact: true })).toHaveCount(0);
});

test("agency member remains view-only even if a stale capability claims writes are ready", async ({ page }) => {
  await installBaseApi(page, "agency", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(readiness({ role: "agency" })),
      });
      return;
    }
    if (path.endsWith(`/accounts/${ACCOUNT_ID}/budget-targets`)) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(targetResponse) });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], count: 0 }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  }, "member");

  await page.goto("/budgets");
  await expect(page.getByRole("heading", { name: "Бюджеты Meta" })).toBeVisible();
  await expect(page.getByText("Ваша роль может видеть фактические значения", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Новая сумма бюджета Meta")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Подтвердить и отправить в Meta" })).toHaveCount(0);
});

test("missing allowed target policy fails closed even when write flags are true", async ({ page }) => {
  await installBaseApi(page, "solo_client", async (route, path) => {
    if (path === "/provider-controls/meta/readiness") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(readiness({ allowed: undefined })),
      });
      return;
    }
    if (path.endsWith(`/accounts/${ACCOUNT_ID}/budget-targets`)) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(targetResponse) });
      return;
    }
    if (path === "/provider-controls/meta/budget-changes") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], count: 0 }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  await page.goto("/budgets");
  await expect(page.getByText("В аккаунте нет доступных кампаний или групп объявлений", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Новая сумма бюджета Meta")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Проверить изменение" })).toHaveCount(0);
});
