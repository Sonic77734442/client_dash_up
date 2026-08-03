import { expect, test } from "@playwright/test";
import { attachSession, createAdminSession } from "./auth";

test("frontend smoke flow", async ({ page, context, request }) => {
  test.setTimeout(90_000);
  const token = await createAdminSession(request);
  await attachSession(page, context, token);
  await page.goto("/platform");

  await expect(page).toHaveURL(/\/platform$/);
  await expect(page.locator(".topbar-title")).toHaveText("Центр решений администратора", {
    timeout: 30_000,
  });

  await page.getByRole("link", { name: "Открыть метрики" }).click();
  await expect(page).toHaveURL(/\/\?admin_metrics=1$/);
  await expect(page.locator(".topbar-title")).toHaveText("Центр эффективности");
  await expect(page.getByText("Режим наблюдателя администратора")).toBeVisible();

  await page.locator(".filters .chip-btn").filter({ hasText: "7 дней" }).first().click();
  await page.getByRole("button", { name: "Применить" }).click();

  await page.getByRole("button", { name: "Портфель клиентов" }).click();
  await expect(page.getByText("Клиенты в выборке")).toBeVisible();

  const openClientButtons = page.locator(".open-client-btn");
  if ((await openClientButtons.count()) > 0) {
    await openClientButtons.first().click();
    await expect(page).toHaveURL(/\/client\/[^/]+$/);
    await expect(page.getByText("Главное за период", { exact: true })).toBeVisible();
  }
});
