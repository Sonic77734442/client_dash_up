import { expect, test } from "@playwright/test";
import { attachSession, createAgencySessionWithAccess } from "./auth";

test("agency workspace routes are stable and role-scoped", async ({ page, context, request }) => {
  const token = await createAgencySessionWithAccess(request);
  await attachSession(page, context, token);

  await page.goto("/");
  await expect(page.getByText("Platform Admin")).toHaveCount(0);
  await expect(page.locator(".topbar-title").first()).toBeVisible();

  await expect(page.getByRole("link", { name: "Clients" }).first()).toBeVisible();

  const routes: Array<{ path: string; text: string }> = [
    { path: "/integrations", text: "Integrations Hub" },
    { path: "/sync-monitor", text: "Sync Monitor" },
    { path: "/budgets", text: "Accounts Ledger" },
  ];

  for (const route of routes) {
    await page.goto(route.path);
    await expect(page.locator(".topbar-title").filter({ hasText: route.text })).toBeVisible({ timeout: 30000 });
  }
});
