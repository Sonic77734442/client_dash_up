import { expect, test, type Page } from "@playwright/test";

const auth = (access_state: string, permissions = ["dash.analytics", "dash.analytics.read"]) => ({
  user: { id: "id-principal", name: "ID user", role: "client", status: "active" },
  session: {
    valid: true, user_id: "id-principal", role: "client", global_access: false,
    accessible_client_ids: access_state === "ready" ? ["id-client"] : [],
    auth_source: "envidicy_id",
    authority: {
      access_state, product: "dash.analytics", organization_id: "org", project_id: "project", permissions,
      my_url: "https://my.envidicy.com/products",
    },
  },
});

async function readyApi(page: Page, getAuth: () => unknown, onData?: () => void) {
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/backend", "");
    let body: unknown = { items: [] };
    if (path === "/auth/me") body = getAuth();
    else {
      onData?.();
      if (path === "/clients") body = { items: [{ id: "id-client", name: "ID project client", default_currency: "USD", status: "active" }] };
      else if (path === "/insights/overview") body = {
        range: { date_from: "2026-09-01", date_to: "2026-09-15", as_of_date: "2026-09-15" },
        scope: { client_id: "id-client", account_id: null },
        spend_summary: { currency: "USD", spend: 0, conversions: 0 },
        budget_summary: { spend: 0, budget: null, pace_status: "no_budget" },
        breakdowns: { platforms: [], accounts: [] },
      };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

for (const state of ["disabled", "legacy", "unsafe", "enabled"] as const) {
  test(`Envidicy login entry ${state} keeps legacy login available`, async ({ page }) => {
    await page.route("**/api/backend/auth/me", (route) => route.fulfill({ status: 401, body: "{}" }));
    await page.route("**/api/backend/auth/envidicy/config", (route) => route.fulfill({
      status: state === "legacy" ? 404 : 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: state !== "disabled", login_url: state === "unsafe" ? "https://evil.test" : "/api/backend/auth/envidicy/start" }),
    }));
    await page.goto("/login?next=%2Fportal%2Freports");
    await expect(page.getByRole("button", { name: "Войти через Facebook" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Войти через Google" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Войти", exact: true })).toBeVisible();
    const entry = page.getByRole("link", { name: "Войти через Envidicy ID" });
    if (state === "enabled") await expect(entry).toHaveAttribute("href", "/api/backend/auth/envidicy/start?next=%2Fportal%2Freports");
    else await expect(entry).toHaveCount(0);
  });
}

async function expectNoLocalLogin(page: Page) {
  await expect(page.getByLabel("Email", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Пароль", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Придумайте пароль")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Принять приглашение", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Войти через Facebook" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Войти через Google" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Перенести Facebook-вход" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Войти по токену" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Установить пароль" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Нужен доступ?" })).toHaveCount(0);
}

for (const enabled of [true, false]) {
  test(`ID-only policy hides all local login paths when ID is ${enabled ? "available" : "unavailable"}`, async ({ page }) => {
    let authWrites = 0;
    await page.route("**/api/backend/auth/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/envidicy/config")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        enabled, local_auth_enabled: false, login_url: "/api/backend/auth/envidicy/start",
      }) });
      if (route.request().method() !== "GET") authWrites += 1;
      return route.fulfill({ status: 401, body: "{}" });
    });
    await page.goto("/login?invite_token=legacy-invite&oauth_error=facebook_migration_required&next=%2Fportal%2Freports");
    if (enabled) {
      await expect(page.getByRole("link", { name: "Войти через Envidicy ID" })).toHaveAttribute("href", "/api/backend/auth/envidicy/start?next=%2Fportal%2Freports");
      await expect(page.getByText(/Локальные пароли и приглашения здесь больше не используются/)).toBeVisible();
    } else {
      await expect(page.getByText(/Вход через Envidicy ID временно недоступен/)).toBeVisible();
      await expect(page.getByRole("button", { name: "Повторить проверку входа" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Войти через Envidicy ID" })).toHaveCount(0);
    }
    await expectNoLocalLogin(page);
    await expect(page).toHaveURL(/\/login\?/);
    expect(authWrites).toBe(0);
  });
}

for (const failure of ["503", "network", "invalid"] as const) {
  test(`login policy ${failure} fails closed until an explicit retry confirms ID-only`, async ({ page }) => {
    let attempts = 0;
    await page.route("**/api/backend/auth/me", (route) => route.fulfill({ status: 401, body: "{}" }));
    await page.route("**/api/backend/auth/envidicy/config", (route) => {
      attempts += 1;
      if (attempts === 1) {
        if (failure === "network") return route.abort("failed");
        return route.fulfill({ status: failure === "503" ? 503 : 200, contentType: "application/json", body: JSON.stringify({ enabled: true, local_auth_enabled: "false" }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        enabled: true, local_auth_enabled: false, login_url: "/api/backend/auth/envidicy/start",
      }) });
    });
    await page.goto("/login?invite_token=old-invite");
    await expect(page.getByText(/Не удалось проверить способ входа/)).toBeVisible();
    await expectNoLocalLogin(page);
    expect(attempts).toBe(1);
    await page.getByRole("button", { name: "Повторить проверку входа" }).click();
    await expect(page.getByRole("link", { name: "Войти через Envidicy ID" })).toBeVisible();
    await expectNoLocalLogin(page);
    expect(attempts).toBe(2);
    await expect(page).toHaveURL(/\/login\?invite_token=old-invite$/);
  });
}

test("pending login policy never briefly exposes password or invite controls", async ({ page }) => {
  let releaseConfig: () => void = () => {};
  const waiting = new Promise<void>((resolve) => { releaseConfig = resolve; });
  await page.route("**/api/backend/auth/me", (route) => route.fulfill({ status: 401, body: "{}" }));
  await page.route("**/api/backend/auth/envidicy/config", async (route) => {
    await waiting;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      enabled: true, local_auth_enabled: false, login_url: "/api/backend/auth/envidicy/start",
    }) });
  });
  await page.goto("/login?invite_token=old-invite", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Проверяем способ входа…")).toBeVisible();
  await expectNoLocalLogin(page);
  releaseConfig();
  await expect(page.getByRole("link", { name: "Войти через Envidicy ID" })).toBeVisible();
  await expectNoLocalLogin(page);
});

test("dead registration page redirects to policy-owned login and preserves only a safe destination", async ({ page }) => {
  let legacyWrites = 0;
  let localAuthEnabled = false;
  await page.route("**/api/backend/**", async (route) => {
    if (route.request().method() !== "GET") legacyWrites += 1;
    if (new URL(route.request().url()).pathname.endsWith("/auth/envidicy/config")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        enabled: true, local_auth_enabled: localAuthEnabled, login_url: "/api/backend/auth/envidicy/start",
      }) });
    }
    return route.fulfill({ status: 401, body: "{}" });
  });
  await page.goto("/register?next=%2Fportal%2Freports&invite_token=old-invite&token=not-forwarded");
  await expect(page).toHaveURL(/\/login\?next=%2Fportal%2Freports$/);
  await expect(page.getByRole("link", { name: "Войти через Envidicy ID" })).toHaveAttribute("href", "/api/backend/auth/envidicy/start?next=%2Fportal%2Freports");
  await expect(page.getByRole("button", { name: "Отправить код" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Создать аккаунт" })).toHaveCount(0);
  await expectNoLocalLogin(page);
  for (const next of ["https://evil.test", "//evil.test", "/\\evil.test", "/register", "/login/success"]) {
    await page.goto(`/register?${new URLSearchParams({ next })}`);
    await expect(page).toHaveURL(/\/login$/);
  }
  await page.goto("/register?next=%2Fportal&next=%2Fbudgets");
  await expect(page).toHaveURL(/\/login$/);
  localAuthEnabled = true;
  await page.goto("/register");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("Пароль", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Войти через Facebook" })).toBeVisible();
  expect(legacyWrites).toBe(0);
});

test("My launch redirects through the fixed same-origin code flow without URL-carried authority", async ({ request }) => {
  // Inspect this service's redirect, not a downstream ID/disabled-login response.
  const response = await request.get("/auth/envidicy?next=%2Fportal%2Fbilling&project_id=forged&organization_id=forged&access_token=not-forwarded", { maxRedirects: 0 });
  expect(response.status()).toBe(303);
  expect(response.headers().location).toBe("/api/backend/auth/envidicy/start?next=%2Fportal%2Fbilling");
  expect(response.headers()["cache-control"]).toBe("no-store");
});

for (const state of ["not_granted", "project_unlinked", "context_unavailable"]) {
  test(`ID session ${state} shows My guidance without login loops or protected requests`, async ({ page }) => {
    let dataRequests = 0;
    await readyApi(page, () => auth(state), () => { dataRequests += 1; });
    await page.goto("/accounts?client_id=not-authority");
    await expect(page.getByRole("heading", { name: state === "context_unavailable" ? "Не удалось проверить доступ в My" : "Доступ к Dash настраивается в My" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Открыть My" })).toHaveAttribute("href", "https://my.envidicy.com/products");
    await expect(page).toHaveURL(/\/accounts\?client_id=not-authority$/);
    await expect(page.getByRole("navigation", { name: "Основная навигация" })).toHaveCount(0);
    expect(dataRequests).toBe(0);
  });
}

test("retrying My authority opens a granted project and keeps ID client budgets read-only", async ({ page }) => {
  let state = "not_granted";
  await readyApi(page, () => auth(state));
  await page.goto("/portal/billing");
  await expect(page.getByRole("heading", { name: "Доступ к Dash настраивается в My" })).toBeVisible();
  state = "ready";
  await page.getByRole("button", { name: "Повторить проверку доступа" }).click();
  await expect(page.getByRole("link", { name: "My Envidicy" })).toHaveAttribute("href", "https://my.envidicy.com/products");
  await expect(page.getByRole("link", { name: "Плановые бюджеты", exact: true })).toHaveAttribute("href", "/portal/billing");
  await expect(page.getByRole("button", { name: "Создать плановый бюджет" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Подтвердить и отправить в Meta" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/portal\/billing$/);
});

for (const manage of [false, true]) {
  test(`My manage permission ${manage ? "allows local budgets only" : "does not expose budget mutations"}`, async ({ page }) => {
    await readyApi(page, () => auth("ready", manage ? ["dash.analytics", "dash.analytics.read", "dash.analytics.manage"] : ["dash.analytics", "dash.analytics.read"]));
    await page.goto("/budgets");
    if (manage) {
      await expect(page).toHaveURL(/\/budgets$/);
      await expect(page.getByRole("button", { name: "Создать плановый бюджет", exact: true })).toBeVisible();
      await expect(page.getByRole("link", { name: "Плановые бюджеты", exact: true })).toHaveAttribute("href", "/budgets");
      await expect(page.getByRole("button", { name: "Подтвердить и отправить в Meta" })).toHaveCount(0);
      await page.goto("/platform");
      await expect(page).toHaveURL(/\/portal$/);
    } else {
      await expect(page).toHaveURL(/\/portal$/);
      await expect(page.getByRole("button", { name: "Создать плановый бюджет", exact: true })).toHaveCount(0);
    }
  });
}

test("a My revocation from a protected API read refreshes authority without logging out", async ({ page }) => {
  let state = "ready";
  let authCalls = 0;
  let authCallsBeforeDenial = 0;
  let deniedReads = 0;
  await readyApi(page, () => { authCalls += 1; return auth(state); });
  await page.route("**/api/backend/clients?**", async (route) => {
    authCallsBeforeDenial = authCalls;
    state = "not_granted";
    deniedReads += 1;
    await route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({
      error: { code: "envidicy_access_required", message: "Dash access must be confirmed in My" },
    }) });
  });
  await page.goto("/portal");
  await expect(page.getByRole("heading", { name: "Доступ к Dash настраивается в My" })).toBeVisible();
  await expect(page).toHaveURL(/\/portal$/);
  expect(deniedReads).toBe(1);
  expect(authCalls).toBe(authCallsBeforeDenial + 1);
  await expect(page.getByRole("link", { name: "Открыть My" })).toBeVisible();
});
