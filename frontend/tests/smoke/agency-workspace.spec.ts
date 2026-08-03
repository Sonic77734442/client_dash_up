import { expect, test } from "@playwright/test";
import { attachSession, createAgencySessionWithAccess } from "./auth";

test("agency workspace routes are stable and role-scoped", async ({ page, context, request }) => {
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
});
