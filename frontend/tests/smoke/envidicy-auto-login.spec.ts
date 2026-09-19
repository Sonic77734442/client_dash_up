import { expect, test, type Page, type Route } from "@playwright/test";
import { ENVIDICY_AUTO_LOGIN_KEY, ENVIDICY_MY_URL } from "../../lib/envidicyAuth";

function idSession(state = "ready", redirectToMy = false) {
  return {
    user: { id: "id-user", name: "Test ID user", role: "client", status: "active" },
    session: {
      valid: true, role: "client", user_id: "id-user", global_access: false,
      accessible_client_ids: state === "ready" ? ["test-client"] : [], auth_source: "envidicy_id",
      authority: { access_state: state, redirect_to_my: redirectToMy, product: "dash.analytics",
        organization_id: "test-org", project_id: "test-project", permissions: ["dash.analytics.read"],
        my_url: "https://untrusted.example/ignored" },
    },
  };
}

type Mock = {
  me: ReturnType<typeof idSession> | null;
  meStatus: number;
  localAuth: boolean;
  autoLogin: boolean;
  starts: string[];
  dataRequests: number;
  beforeMe?: () => Promise<void>;
  onStart?: (route: Route) => Promise<void>;
};

async function mockApi(page: Page, options: Partial<Mock> = {}): Promise<Mock> {
  const mock: Mock = { me: null, meStatus: 401, localAuth: false, autoLogin: true, starts: [], dataRequests: 0, ...options };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/backend", "");
    if (path === "/auth/envidicy/start") {
      mock.starts.push(route.request().url());
      if (mock.onStart) return mock.onStart(route);
      return route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Test ID entry: sign in or register</h1>" });
    }
    if (path === "/auth/envidicy/config") return route.fulfill({ status: 200, json: {
      enabled: true, auto_login: mock.autoLogin, local_auth_enabled: mock.localAuth,
      login_url: "/api/backend/auth/envidicy/start",
    } });
    if (path === "/auth/me") {
      await mock.beforeMe?.();
      return route.fulfill({ status: mock.meStatus, json: mock.me || {} });
    }
    if (path === "/auth/logout") {
      mock.me = null;
      mock.meStatus = 401;
      return route.fulfill({ status: 200, json: { status: "ok" } });
    }
    if (path === "/auth/csrf") return route.fulfill({ status: 200, json: { csrf_token: "test-csrf" } });
    mock.dataRequests += 1;
    let body: unknown = { items: [] };
    if (path === "/clients") body = { items: [{ id: "test-client", name: "Test client", default_currency: "USD" }] };
    if (path === "/insights/overview") body = {
      range: { date_from: "2026-09-01", date_to: "2026-09-15", as_of_date: "2026-09-15" },
      scope: { client_id: "test-client", account_id: null },
      spend_summary: { currency: "USD", spend: 0, conversions: 0 },
      budget_summary: { spend: 0, budget: null, pace_status: "no_budget" },
      breakdowns: { platforms: [], accounts: [] },
    };
    return route.fulfill({ status: 200, json: body });
  });
  return mock;
}

test("automatic ID login restores a deep link including query and fragment after the callback", async ({ page }) => {
  const target = "/portal/billing?period=30#spend";
  const mock = await mockApi(page);
  mock.onStart = async (route) => {
    mock.me = idSession();
    mock.meStatus = 200;
    await route.fulfill({ status: 303, headers: { Location: target } });
  };
  await page.goto(target);
  await expect(page.getByRole("navigation", { name: "Основная навигация" })).toBeVisible();
  await expect(page).toHaveURL(new RegExp("/portal/billing\\?period=30#spend$"));
  expect(mock.starts).toHaveLength(1);
  expect(new URL(mock.starts[0]).searchParams.get("next")).toBe(target);
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), ENVIDICY_AUTO_LOGIN_KEY)).toBeNull();
});

test("the former register route starts the same ID sign-in/registration flow", async ({ page }) => {
  const mock = await mockApi(page);
  await page.goto("/register?next=%2Fportal%2Freports");
  await expect(page.getByRole("heading", { name: "Test ID entry: sign in or register" })).toBeVisible();
  expect(mock.starts).toHaveLength(1);
  expect(new URL(mock.starts[0]).searchParams.get("next")).toBe("/portal/reports");
});

test("automatic entry waits for the session check and never restarts an existing session", async ({ page }) => {
  let release: () => void = () => {};
  const waiting = new Promise<void>((resolve) => { release = resolve; });
  const mock = await mockApi(page, { me: idSession(), meStatus: 200, beforeMe: () => waiting });
  await page.goto("/login?next=%2Fportal%2Fbilling", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("link", { name: "Войти через Envidicy ID" })).toBeVisible();
  expect(mock.starts).toHaveLength(0);
  release();
  await expect(page.getByRole("navigation", { name: "Основная навигация" })).toBeVisible();
  await expect(page).toHaveURL(/\/portal\/billing$/);
  expect(mock.starts).toHaveLength(0);
});

test("a callback without an accepted session cookie cannot repeat automatic login, including after reload", async ({ page }) => {
  const mock = await mockApi(page);
  mock.onStart = async (route) => route.fulfill({ status: 303, headers: { Location: "/portal/billing" } });
  await page.goto("/portal/billing");
  await expect(page.getByText(/Автоматический вход приостановлен/)).toBeVisible();
  expect(mock.starts).toHaveLength(1);
  await page.reload();
  await expect(page.getByText(/Автоматический вход приостановлен/)).toBeVisible();
  expect(mock.starts).toHaveLength(1);
  await page.getByRole("link", { name: "Войти через Envidicy ID" }).click();
  await expect.poll(() => mock.starts.length).toBe(2);
  await expect(page.getByText(/Автоматический вход приостановлен/)).toBeVisible();
  expect(mock.starts).toHaveLength(2);
});

test("an existing legacy session stays in Dash even while automatic ID entry is enabled", async ({ page }) => {
  const me = idSession();
  me.session.auth_source = "legacy";
  const mock = await mockApi(page, { me, meStatus: 200, localAuth: true });
  await page.goto("/login?next=%2Fportal%2Fbilling");
  await expect(page.getByRole("navigation", { name: "Основная навигация" })).toBeVisible();
  await expect(page).toHaveURL(/\/portal\/billing$/);
  expect(mock.starts).toHaveLength(0);
});

test("a protected read revocation refreshes authority once and redirects only the verified denial to My", async ({ page }) => {
  const mock = await mockApi(page, { me: idSession(), meStatus: 200 });
  let deniedReads = 0;
  await page.route("https://my.envidicy.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Test My products</h1>" }));
  await page.route("**/api/backend/clients?**", (route) => {
    mock.me = idSession("not_granted", true);
    deniedReads += 1;
    return route.fulfill({ status: 403, json: { error: { code: "envidicy_access_required", message: "Test access revoked" } } });
  });
  await page.goto("/portal");
  await expect(page.getByRole("heading", { name: "Test My products" })).toBeVisible();
  await expect(page).toHaveURL(ENVIDICY_MY_URL);
  expect(deniedReads).toBe(1);
  expect(mock.starts).toHaveLength(0);
});

for (const code of ["envidicy_auth_failed", "envidicy_pilot_only", "access_denied", "unknown_error", ""]) {
  test(`callback error ${code || "empty"} never automatically restarts ID`, async ({ page }) => {
    const mock = await mockApi(page, { localAuth: true });
    await page.goto(`/login?${new URLSearchParams({ oauth_error: code, next: "/portal/reports?period=30#spend" })}`);
    const legacy = page.getByRole("link", { name: "Использовать прежний вход в Dash" });
    await expect(legacy).toBeVisible();
    await expect(page.getByRole("link", { name: "Войти через Envidicy ID" })).toBeVisible();
    expect(mock.starts).toHaveLength(0);
    await legacy.click();
    await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("next")).toBe("/portal/reports?period=30#spend");
    expect(mock.starts).toHaveLength(0);
  });
}

for (const localAuth of [true, false]) {
  test(`legacy opt-out is ${localAuth ? "allowed only during transition" : "ignored in ID-only mode"}`, async ({ page }) => {
    const mock = await mockApi(page, { localAuth });
    await page.goto("/login?legacy=1&next=%2Fportal");
    if (localAuth) {
      await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
      expect(mock.starts).toHaveLength(0);
    } else {
      await expect(page.getByRole("heading", { name: "Test ID entry: sign in or register" })).toBeVisible();
      expect(mock.starts).toHaveLength(1);
    }
  });
}

test("session outage leaves a retry screen instead of interpreting it as logout or My denial", async ({ page }) => {
  const mock = await mockApi(page, { meStatus: 503 });
  await page.goto("/login?next=%2Fportal%2Freports");
  await expect(page.getByText("Не удалось проверить сессию. Автоматический вход приостановлен.")).toBeVisible();
  expect(mock.starts).toHaveLength(0);
  await expect(page.getByRole("button", { name: "Повторить проверку сессии" })).toBeEnabled();
  await expect(page).toHaveURL(/\/login\?/);
});

test("unavailable sessionStorage disables automatic entry but keeps manual ID entry usable", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, "sessionStorage", { configurable: true, get() { throw new Error("Storage is blocked"); } }));
  const mock = await mockApi(page);
  await page.goto("/login");
  await expect(page.getByText(/Автоматический вход приостановлен/)).toBeVisible();
  expect(mock.starts).toHaveLength(0);
  await page.getByRole("link", { name: "Войти через Envidicy ID" }).click();
  await expect(page.getByRole("heading", { name: "Test ID entry: sign in or register" })).toBeVisible();
  expect(mock.starts).toHaveLength(1);
});

for (const path of ["/portal", "/login?next=%2Fportal"]) {
  test(`verified My denial redirects from ${path} to only the fixed My destination`, async ({ page }) => {
    const mock = await mockApi(page, { me: idSession("not_granted", true), meStatus: 200 });
    await page.route("https://my.envidicy.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Test My products</h1>" }));
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Test My products" })).toBeVisible();
    await expect(page).toHaveURL(ENVIDICY_MY_URL);
    expect(mock.dataRequests).toBe(0);
    expect(mock.starts).toHaveLength(0);
  });
}

for (const state of ["project_unlinked", "context_unavailable"]) {
  test(`${state} never redirects to My even with a stale redirect marker`, async ({ page }) => {
    const mock = await mockApi(page, { me: idSession(state, true), meStatus: 200 });
    await page.goto("/portal/billing?period=30#spend");
    await expect(page.getByRole("button", { name: "Повторить проверку доступа" })).toBeVisible();
    await expect(page).toHaveURL(/\/portal\/billing\?period=30#spend$/);
    expect(mock.dataRequests).toBe(0);
    expect(mock.starts).toHaveLength(0);
  });
}

for (const state of ["ready", "project_unlinked"]) {
  test(`explicit logout from ${state === "ready" ? "the sidebar" : "the access gate"} does not silently restore the ID session`, async ({ page }) => {
    const mock = await mockApi(page, { me: idSession(state), meStatus: 200 });
    await page.goto("/portal/billing");
    await page.getByRole("button", { name: state === "ready" ? "Выйти" : "Выйти из Dash", exact: true }).click();
    await expect(page.getByText(/Автоматический вход приостановлен/)).toBeVisible();
    await expect(page).toHaveURL(/\/login(?:\?|$)/);
    expect(mock.starts).toHaveLength(0);
    await page.reload();
    await expect(page.getByText(/Автоматический вход приостановлен/)).toBeVisible();
    expect(mock.starts).toHaveLength(0);
  });
}
