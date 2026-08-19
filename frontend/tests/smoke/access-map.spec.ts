import { expect, test, type APIRequestContext } from "@playwright/test";
import { attachSession, createAdminSession } from "./auth";

const API_BASE = "http://127.0.0.1:8000";

async function requireJson(
  response: Awaited<ReturnType<APIRequestContext["post"]>>,
  label: string,
) {
  if (!response.ok()) throw new Error(`${label}:${response.status()}:${await response.text()}`);
  return response.json() as Promise<Record<string, string>>;
}

test("admin access map explains effective rights and uses the correct management flow", async ({
  page,
  context,
  request,
}) => {
  test.setTimeout(90_000);
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const adminToken = await createAdminSession(request);
  const headers = { Authorization: `Bearer ${adminToken}` };
  const directClientName = `Direct client ${stamp}`;
  const agencyClientName = `Agency client ${stamp}`;
  const viewerName = `Viewer ${stamp}`;
  const agencyUserName = `Agency user ${stamp}`;

  const directClient = await requireJson(await request.post(`${API_BASE}/clients`, {
    headers,
    data: { name: directClientName, status: "active", default_currency: "USD" },
  }), "create_direct_client");
  const agencyClient = await requireJson(await request.post(`${API_BASE}/clients`, {
    headers,
    data: { name: agencyClientName, status: "active", default_currency: "USD" },
  }), "create_agency_client");
  const viewer = await requireJson(await request.post(`${API_BASE}/auth/internal/users`, {
    data: {
      email: `viewer-${stamp}@test.local`,
      name: viewerName,
      role: "client",
      status: "active",
    },
  }), "create_viewer");
  const agencyUser = await requireJson(await request.post(`${API_BASE}/auth/internal/users`, {
    data: {
      email: `agency-${stamp}@test.local`,
      name: agencyUserName,
      role: "agency",
      status: "active",
    },
  }), "create_agency_user");
  const agency = await requireJson(await request.post(`${API_BASE}/platform/agencies`, {
    headers,
    data: { name: `Access agency ${stamp}`, status: "active", plan: "starter" },
  }), "create_agency");
  await requireJson(await request.post(`${API_BASE}/platform/agencies/${agency.id}/members`, {
    headers,
    data: { user_id: agencyUser.id, role: "manager", status: "active" },
  }), "assign_member");
  await requireJson(await request.post(`${API_BASE}/platform/agencies/${agency.id}/clients`, {
    headers,
    data: { client_id: agencyClient.id },
  }), "bind_agency_client");

  await attachSession(page, context, adminToken);
  await page.goto("/platform/access");

  await expect(page.getByRole("heading", { name: "Права складываются из трёх частей" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("Администратор видит всех клиентов автоматически", { exact: false })).toBeVisible();
  await expect(page.getByText(
    "Владелец или менеджер подключает Meta Ads и Google Ads и ищет новые аккаунты.",
    { exact: true },
  )).toBeVisible();
  await expect(page.getByText("Уровень", { exact: true })).toHaveCount(0);

  const userSelect = page.getByLabel("Пользователь для доступа");
  await userSelect.selectOption(viewer.id);
  await expect(page.getByText("Только просмотр данных клиента", { exact: true })).toBeVisible();
  const clientSelect = page.getByLabel("Клиент для доступа");
  await clientSelect.selectOption(directClient.id);
  await page.getByRole("button", { name: "Открыть доступ" }).click();

  const viewerRow = page.getByRole("row").filter({ hasText: viewerName }).filter({ hasText: directClientName });
  await expect(viewerRow).toBeVisible();
  await expect(viewerRow.getByText("Клиент · только просмотр", { exact: true })).toBeVisible();
  await expect(viewerRow.getByText("Прямое назначение", { exact: true })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await viewerRow.getByRole("button", { name: "Отозвать" }).click();
  await expect(viewerRow).toHaveCount(0);

  await userSelect.selectOption(agencyUser.id);
  await expect(page.getByText("Работа с клиентом через агентство", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Клиент для доступа")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Перейти в агентства" })).toBeVisible();
  const agencyRow = page.getByRole("row").filter({ hasText: agencyUserName }).filter({ hasText: agencyClientName });
  await expect(agencyRow).toBeVisible();
  await expect(agencyRow.getByRole("link", { name: "К агентствам" })).toHaveAttribute("href", "/platform/agencies");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Права складываются из трёх частей" })).toBeVisible();
  await expect(page.getByText("На телефоне таблицу можно двигать", { exact: false })).toBeVisible();
  await expect(agencyRow.getByRole("link", { name: "К агентствам" })).toBeVisible();
  const pageWidth = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.document).toBeLessThanOrEqual(pageWidth.viewport + 1);
});
