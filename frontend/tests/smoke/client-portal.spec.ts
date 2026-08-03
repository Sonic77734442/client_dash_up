import { expect, test } from "@playwright/test";
import { attachSession, createClientSessionWithAccess } from "./auth";

test("client portal shell is read-only", async ({ page, context, request }) => {
  const token = await createClientSessionWithAccess(request);
  await attachSession(page, context, token);
  await page.goto("/portal");

  await expect(page.locator(".topbar-title")).toHaveText("Результаты рекламы");
  await expect(page.getByText(/клиентский кабинет/i).first()).toBeVisible();

  await expect(page.getByRole("link", { name: "Источники рекламы" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Синхронизация" })).toHaveCount(0);
  await expect(page.getByText("Создать бюджет", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Переподключить", { exact: true })).toHaveCount(0);
});
