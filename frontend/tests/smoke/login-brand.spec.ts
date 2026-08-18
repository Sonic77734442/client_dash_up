import { expect, test, type Page } from "@playwright/test";

async function useRussianLocale(page: Page) {
  await page.addInitScript(() => localStorage.setItem("ops_locale", "ru"));
}

test.beforeEach(async ({ page }) => {
  await useRussianLocale(page);
});

test("branded login exposes the Envidicy product switcher and both sign-in providers", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByRole("link", { name: "Envidicy", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Управляйте рекламными операциями" })).toBeVisible();

  const products = page.getByRole("navigation", { name: "Продукты Envidicy" });
  await expect(products).toBeVisible();
  await expect(products.getByRole("link", { name: "App Envidicy" })).toHaveAttribute(
    "href",
    "https://app.envidicy.kz",
  );
  await expect(products.getByText("Dash Envidicy", { exact: true })).toHaveAttribute("aria-current", "page");
  await expect(products.getByRole("link", { name: "CRM Envidicy" })).toHaveAttribute(
    "href",
    "https://crm.envidicy.kz",
  );

  await expect(page.getByRole("button", { name: "Войти через Facebook" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Войти через Google" })).toBeVisible();
});

for (const provider of [
  { button: "Войти через Facebook", source: "m" },
  { button: "Войти через Google", source: "g" },
] as const) {
  test(`${provider.button} preserves the neutral same-origin OAuth relay contract`, async ({ page }) => {
    await page.route("**/api/connect/start?**", async (route) => {
      await route.fulfill({ status: 200, contentType: "text/html", body: "OAuth relay captured" });
    });
    await page.goto("/login?next=%2Fportal%2Freports");

    const requestPromise = page.waitForRequest((request) =>
      request.url().includes("/api/connect/start?"),
    );
    await page.getByRole("button", { name: provider.button }).click();
    const oauthUrl = new URL((await requestPromise).url());

    expect(oauthUrl.pathname).toBe("/api/connect/start");
    expect(oauthUrl.searchParams.get("source")).toBe(provider.source);
    expect(oauthUrl.searchParams.get("intent")).toBe("login");
    expect(oauthUrl.searchParams.get("next")).toBe("/portal/reports");
  });
}

test("pressing Enter submits the password form and renders a mocked API error", async ({ page }) => {
  let loginPayload: unknown = null;
  await page.route("**/api/backend/auth/password/login", async (route) => {
    loginPayload = route.request().postDataJSON();
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "Тестовый ответ авторизации" } }),
    });
  });
  await page.goto("/login");

  await page.getByLabel("Email").fill("  USER@Example.COM ");
  await page.getByLabel("Пароль", { exact: true }).fill("password-123");
  await page.getByLabel("Пароль", { exact: true }).press("Enter");

  await expect(page.locator("p[role=alert]")).toHaveText("Тестовый ответ авторизации");
  expect(loginPayload).toEqual({ email: "user@example.com", password: "password-123" });
  await expect(page.getByRole("button", { name: "Войти", exact: true })).toBeEnabled();
});

test("invite token switches the branded panel to invitation acceptance", async ({ page }) => {
  await page.goto("/login?invite_token=invite-fixture");

  await expect(page.getByRole("heading", { name: "Принять приглашение" })).toBeVisible();
  await expect(page.getByText("Создайте пароль, чтобы войти в рабочее пространство.")).toBeVisible();
  await expect(page.getByLabel("Имя")).toBeVisible();
  await expect(page.getByLabel("Придумайте пароль")).toBeVisible();
  await expect(page.getByRole("button", { name: "Принять приглашение" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Войти через Facebook" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Войти через Google" })).toHaveCount(0);
});

test("mobile login keeps the product navigation usable without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");

  const main = page.getByRole("main");
  await expect(main.getByText("Envidicy", { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Продукты Envidicy" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Управляйте рекламными операциями" })).toBeHidden();
  await expect(page.getByRole("button", { name: "Войти через Facebook" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Войти через Google" })).toBeVisible();

  const sizes = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(sizes.document).toBeLessThanOrEqual(sizes.viewport);
  expect(sizes.body).toBeLessThanOrEqual(sizes.viewport);
});
