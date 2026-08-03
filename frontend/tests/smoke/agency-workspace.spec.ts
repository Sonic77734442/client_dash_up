import { expect, test } from "@playwright/test";
import { attachSession, createAgencySessionWithAccess } from "./auth";

test("agency workspace routes are stable and role-scoped", async ({ page, context, request }) => {
  test.setTimeout(90_000);
  const token = await createAgencySessionWithAccess(request);
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
  const metaConnect = page.getByRole("button", { name: "Подключить Meta Ads" }).first();
  await expect(metaConnect).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Войти через Facebook" })).toHaveCount(0);
  await metaConnect.click();
  await expect(page.getByRole("heading", { name: "Подключить Meta Ads" })).toBeVisible();

  await page.route("**/auth/facebook/start?**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "ok" });
  });
  const requestPromise = page.waitForRequest((oauthRequest) =>
    oauthRequest.url().includes("/auth/facebook/start?"),
  );
  await page.getByRole("button", { name: "Перейти к авторизации" }).click();
  const oauthRequest = await requestPromise;
  const oauthUrl = new URL(oauthRequest.url());

  expect(oauthUrl.pathname).toMatch(/\/auth\/facebook\/start$/);
  expect(oauthUrl.searchParams.get("intent")).toBe("connect");
  expect(oauthUrl.searchParams.get("connect_mode")).toBe("add");
});
