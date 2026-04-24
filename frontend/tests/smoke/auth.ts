import type { APIRequestContext, BrowserContext, Page } from "@playwright/test";

const API_BASE = "http://127.0.0.1:8000";

async function postWithRateLimitRetry(
  request: APIRequestContext,
  url: string,
  options: { data?: unknown; headers?: Record<string, string> },
  maxAttempts = 5
) {
  let attempt = 0;
  // Lightweight retry for shared smoke backend with strict auth rate limits.
  while (attempt < maxAttempts) {
    attempt += 1;
    const res = await request.post(url, options);
    if (res.status() !== 429 || attempt >= maxAttempts) return res;
    const waitMs = 250 * attempt;
    await new Promise((resolve) => setTimeout(resolve, waitMs));
  }
  return request.post(url, options);
}

async function createUser(request: APIRequestContext, role: "admin" | "agency" | "client", email: string) {
  const res = await postWithRateLimitRetry(request, `${API_BASE}/auth/internal/users`, {
    data: { email, name: `${role}-smoke`, role, status: "active" },
  });
  if (!res.ok()) throw new Error(`create_user_failed:${res.status()}`);
  return (await res.json()) as { id: string };
}

async function issueToken(request: APIRequestContext, userId: string) {
  const res = await postWithRateLimitRetry(request, `${API_BASE}/auth/internal/sessions/issue`, {
    data: { user_id: userId, ttl_minutes: 60 },
  });
  if (!res.ok()) throw new Error(`issue_token_failed:${res.status()}`);
  const body = (await res.json()) as { token: string };
  return body.token;
}

export async function attachSession(page: Page, context: BrowserContext, token: string) {
  await context.addCookies([
    {
      name: "ops_session",
      value: token,
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
    },
  ]);
  await page.addInitScript((apiBase: string) => {
    localStorage.setItem("ops_api_base", apiBase);
    localStorage.removeItem("ops_session_token");
  }, API_BASE);
}

export async function createAdminSession(request: APIRequestContext) {
  const email = `admin-smoke-${Date.now()}-${Math.random().toString(16).slice(2)}@test.local`;
  const user = await createUser(request, "admin", email);
  return issueToken(request, user.id);
}

export async function createClientSessionWithAccess(request: APIRequestContext) {
  const adminToken = await createAdminSession(request);
  const adminAuth = { Authorization: `Bearer ${adminToken}` };

  const clientRes = await request.post(`${API_BASE}/clients`, {
    headers: adminAuth,
    data: { name: `tenant-${Date.now()}`, status: "active", default_currency: "USD" },
  });
  if (!clientRes.ok()) throw new Error(`create_client_failed:${clientRes.status()}`);
  const client = (await clientRes.json()) as { id: string };

  const email = `client-smoke-${Date.now()}-${Math.random().toString(16).slice(2)}@test.local`;
  const user = await createUser(request, "client", email);

  const grantRes = await postWithRateLimitRetry(request, `${API_BASE}/auth/internal/access`, {
    headers: adminAuth,
    data: { user_id: user.id, client_id: client.id, role: "client" },
  });
  if (!grantRes.ok()) throw new Error(`assign_access_failed:${grantRes.status()}`);

  return issueToken(request, user.id);
}

export async function createAgencySessionWithAccess(request: APIRequestContext) {
  const adminToken = await createAdminSession(request);
  const adminAuth = { Authorization: `Bearer ${adminToken}` };

  const clientRes = await request.post(`${API_BASE}/clients`, {
    headers: adminAuth,
    data: { name: `agency-tenant-${Date.now()}`, status: "active", default_currency: "USD" },
  });
  if (!clientRes.ok()) throw new Error(`create_client_failed:${clientRes.status()}`);
  const client = (await clientRes.json()) as { id: string };

  const email = `agency-smoke-${Date.now()}-${Math.random().toString(16).slice(2)}@test.local`;
  const user = await createUser(request, "agency", email);

  const grantRes = await postWithRateLimitRetry(request, `${API_BASE}/auth/internal/access`, {
    headers: adminAuth,
    data: { user_id: user.id, client_id: client.id, role: "agency" },
  });
  if (!grantRes.ok()) throw new Error(`assign_access_failed:${grantRes.status()}`);

  return issueToken(request, user.id);
}

export async function createClientSessionForExistingClient(request: APIRequestContext, clientId: string) {
  const adminToken = await createAdminSession(request);
  const adminAuth = { Authorization: `Bearer ${adminToken}` };

  const email = `client-smoke-${Date.now()}-${Math.random().toString(16).slice(2)}@test.local`;
  const user = await createUser(request, "client", email);

  const grantRes = await postWithRateLimitRetry(request, `${API_BASE}/auth/internal/access`, {
    headers: adminAuth,
    data: { user_id: user.id, client_id: clientId, role: "client" },
  });
  if (!grantRes.ok()) throw new Error(`assign_access_failed:${grantRes.status()}`);

  return issueToken(request, user.id);
}
