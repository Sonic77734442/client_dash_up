import { expect, test, type APIRequestContext } from "@playwright/test";
import { attachSession, createAdminSession } from "./auth";

const API_BASE = "http://127.0.0.1:8000";

async function requireOk(response: Awaited<ReturnType<APIRequestContext["post"]>>, label: string) {
  if (!response.ok()) throw new Error(`${label}:${response.status()}`);
  return response.json() as Promise<Record<string, unknown>>;
}

async function createMemberFixture(request: APIRequestContext, role: "owner" | "manager" | "member") {
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const adminToken = await createAdminSession(request);
  const headers = { Authorization: `Bearer ${adminToken}` };

  const client = await requireOk(await request.post(`${API_BASE}/clients`, {
    headers,
    data: { name: `Access client ${stamp}`, status: "active", default_currency: "USD" },
  }), "create_client");
  const user = await requireOk(await request.post(`${API_BASE}/auth/internal/users`, {
    data: {
      email: `agency-${role}-${stamp}@test.local`,
      name: `Agency ${role} ${stamp}`,
      role: "agency",
      status: "active",
    },
  }), "create_user");
  const agency = await requireOk(await request.post(`${API_BASE}/platform/agencies`, {
    headers,
    data: { name: `Access agency ${stamp}`, status: "active", plan: "starter" },
  }), "create_agency");
  await requireOk(await request.post(`${API_BASE}/platform/agencies/${agency.id}/members`, {
    headers,
    data: { user_id: user.id, role, status: "active" },
  }), "assign_member");
  await requireOk(await request.post(`${API_BASE}/platform/agencies/${agency.id}/clients`, {
    headers,
    data: { client_id: client.id },
  }), "bind_client");
  await requireOk(await request.post(`${API_BASE}/ad-accounts`, {
    headers,
    data: {
      client_id: client.id,
      platform: "google",
      external_account_id: `ux-${stamp}`,
      name: `Google account ${stamp}`,
      currency: "USD",
      status: "active",
    },
  }), "create_account");
  const session = await requireOk(await request.post(`${API_BASE}/auth/internal/sessions/issue`, {
    data: { user_id: user.id, ttl_minutes: 60 },
  }), "issue_session");

  return {
    token: String(session.token),
    agencyId: String(agency.id),
    clientId: String(client.id),
  };
}

test("admin agency setup defaults the first access holder to owner and manages client bindings", async ({ page, context, request }) => {
  test.setTimeout(60_000);
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const clientName = `Portfolio client ${stamp}`;
  const token = await createAdminSession(request);
  const headers = { Authorization: `Bearer ${token}` };
  const agency = await requireOk(await request.post(`${API_BASE}/platform/agencies`, {
    headers,
    data: { name: `Empty agency ${stamp}`, status: "active", plan: "starter" },
  }), "create_agency");
  const client = await requireOk(await request.post(`${API_BASE}/clients`, {
    headers,
    data: { name: clientName, status: "active", default_currency: "USD" },
  }), "create_client");

  await attachSession(page, context, token);
  await page.goto("/platform/agencies");
  await page.getByText(`Empty agency ${stamp}`, { exact: true }).click();

  await expect(page.getByText("НАСТРОЙКА НЕ ЗАВЕРШЕНА")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Роль участника")).toHaveValue("owner");
  await expect(page.getByLabel("Роль приглашения")).toHaveValue("owner");

  const clientSelect = page.getByLabel("Клиент агентства", { exact: true });
  await clientSelect.selectOption({ label: clientName });
  await page.getByRole("button", { name: "Добавить клиента" }).click();
  await expect(page.getByRole("button", { name: "Убрать из агентства" })).toBeVisible();
  await expect(clientSelect.locator(`option[value="${client.id}"]`)).toHaveCount(0);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Убрать из агентства" }).click();
  await expect(page.getByRole("button", { name: "Убрать из агентства" })).toHaveCount(0);
  await expect(clientSelect.locator(`option[value="${client.id}"]`)).toHaveCount(1);
  expect(String(agency.id)).toBeTruthy();
});

test("agency member cannot manage connections but can sync imported accounts", async ({ page, context, request }) => {
  const fixture = await createMemberFixture(request, "member");
  await attachSession(page, context, fixture.token);

  await page.goto("/sync-monitor");
  await expect(page.getByText("Режим участника", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Подключить Google Ads" }).first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "Найти аккаунты", exact: true }).first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "Обновить данные за 30 дней" })).toBeEnabled();

  await page.goto("/integrations");
  await expect(page.getByText("Режим участника", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "+ Подключить" })).toBeDisabled();
});
