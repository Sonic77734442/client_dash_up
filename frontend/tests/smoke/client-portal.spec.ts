import { expect, test } from "@playwright/test";

test("client portal shell is read-only", async ({ page }) => {
  await page.goto("/portal");

  await expect(page.getByText("Client Overview")).toBeVisible();
  await expect(page.getByText("Client Portal")).toBeVisible();

  await expect(page.getByText("Integrations")).toHaveCount(0);
  await expect(page.getByText("Sync Monitor")).toHaveCount(0);
  await expect(page.getByText("Create Budget")).toHaveCount(0);
  await expect(page.getByText("Reconnect")).toHaveCount(0);
});
