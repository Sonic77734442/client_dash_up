import { expect, test } from "@playwright/test";

test("frontend smoke flow", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator(".topbar-title").filter({ hasText: "Editorial Rigor" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save" })).toBeVisible();

  await page.getByRole("button", { name: "Apply Filters" }).first().click();

  await page.locator("select").first().selectOption("7");
  await page.waitForTimeout(400);

  await page.getByRole("button", { name: "Client Ops Mode" }).click();
  await expect(page.getByText("Active Clients")).toBeVisible();

  const openClientButtons = page.getByRole("button", { name: "OPEN CLIENT" });
  if ((await openClientButtons.count()) > 0) {
    await openClientButtons.first().click();
    await expect(page.getByText("Client ID")).toBeVisible();
    await page.getByRole("button", { name: "CLOSE" }).click();
  }
});
