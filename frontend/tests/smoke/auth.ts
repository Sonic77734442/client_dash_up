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

async function createUser(
  request: APIRequestContext,
  role: "admin" | "agency" | "client" | "solo_client",
  email: string,
) {
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
      url: "http://localhost:5173",
      httpOnly: true,
    },
  ]);
  await page.addInitScript(() => {
    localStorage.setItem("ops_api_base", "/api/backend");
  });
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

export async function createSoloClientSessionWithAccess(request: APIRequestContext) {
  const adminToken = await createAdminSession(request);
  const adminAuth = { Authorization: `Bearer ${adminToken}` };
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`;

  const clientName = `solo-tenant-${stamp}`;
  const clientRes = await request.post(`${API_BASE}/clients`, {
    headers: adminAuth,
    data: { name: clientName, status: "active", default_currency: "USD" },
  });
  if (!clientRes.ok()) throw new Error(`create_solo_client_failed:${clientRes.status()}`);
  const client = (await clientRes.json()) as { id: string };

  const user = await createUser(request, "solo_client", `solo-owner-${stamp}@test.local`);
  const grantRes = await postWithRateLimitRetry(request, `${API_BASE}/auth/internal/access`, {
    headers: adminAuth,
    data: { user_id: user.id, client_id: client.id, role: "client" },
  });
  if (!grantRes.ok()) throw new Error(`assign_solo_access_failed:${grantRes.status()}`);

  const accountRes = await postWithRateLimitRetry(request, `${API_BASE}/ad-accounts`, {
    headers: adminAuth,
    data: {
      client_id: client.id,
      platform: "google",
      external_account_id: `solo-${stamp}`,
      name: `Solo Google Ads ${stamp}`,
      currency: "USD",
      status: "active",
    },
  });
  if (!accountRes.ok()) throw new Error(`create_solo_account_failed:${accountRes.status()}`);
  const account = (await accountRes.json()) as { id: string };

  return {
    token: await issueToken(request, user.id),
    clientId: client.id,
    clientName,
    accountId: account.id,
  };
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

  const agencyRes = await postWithRateLimitRetry(request, `${API_BASE}/platform/agencies`, {
    headers: adminAuth,
    data: { name: `smoke-agency-${Date.now()}`, status: "active", plan: "starter" },
  });
  if (!agencyRes.ok()) throw new Error(`create_agency_failed:${agencyRes.status()}`);
  const agency = (await agencyRes.json()) as { id: string };

  const memberRes = await postWithRateLimitRetry(request, `${API_BASE}/platform/agencies/${agency.id}/members`, {
    headers: adminAuth,
    data: { user_id: user.id, role: "owner", status: "active" },
  });
  if (!memberRes.ok()) throw new Error(`assign_member_failed:${memberRes.status()}`);

  const bindingRes = await postWithRateLimitRetry(request, `${API_BASE}/platform/agencies/${agency.id}/clients`, {
    headers: adminAuth,
    data: { client_id: client.id },
  });
  if (!bindingRes.ok()) throw new Error(`assign_client_failed:${bindingRes.status()}`);

  return issueToken(request, user.id);
}

export async function createMultiAgencySessionWithAccess(request: APIRequestContext) {
  const adminToken = await createAdminSession(request);
  const adminAuth = { Authorization: `Bearer ${adminToken}` };
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const user = await createUser(request, "agency", `multi-agency-${stamp}@test.local`);
  const fixtures: Array<{
    agencyId: string;
    agencyName: string;
    clientId: string;
    clientName: string;
    accountId: string;
    accountName: string;
  }> = [];

  for (const label of ["North", "South"]) {
    const clientName = `${label} client ${stamp}`;
    const clientRes = await postWithRateLimitRetry(request, `${API_BASE}/clients`, {
      headers: adminAuth,
      data: { name: clientName, status: "active", default_currency: "USD" },
    });
    if (!clientRes.ok()) throw new Error(`create_client_failed:${clientRes.status()}`);
    const client = (await clientRes.json()) as { id: string };

    const agencyName = `${label} agency ${stamp}`;
    const agencyRes = await postWithRateLimitRetry(request, `${API_BASE}/platform/agencies`, {
      headers: adminAuth,
      data: { name: agencyName, status: "active", plan: "starter" },
    });
    if (!agencyRes.ok()) throw new Error(`create_agency_failed:${agencyRes.status()}`);
    const agency = (await agencyRes.json()) as { id: string };

    const memberRes = await postWithRateLimitRetry(request, `${API_BASE}/platform/agencies/${agency.id}/members`, {
      headers: adminAuth,
      data: { user_id: user.id, role: "owner", status: "active" },
    });
    if (!memberRes.ok()) throw new Error(`assign_member_failed:${memberRes.status()}`);

    const bindingRes = await postWithRateLimitRetry(request, `${API_BASE}/platform/agencies/${agency.id}/clients`, {
      headers: adminAuth,
      data: { client_id: client.id },
    });
    if (!bindingRes.ok()) throw new Error(`assign_client_failed:${bindingRes.status()}`);

    const accountName = `${label} Ads ${stamp}`;
    const accountRes = await postWithRateLimitRetry(request, `${API_BASE}/ad-accounts`, {
      headers: adminAuth,
      data: {
        client_id: client.id,
        platform: "meta",
        external_account_id: `act_${label.toLowerCase()}_${stamp}`,
        name: accountName,
        currency: "USD",
        status: "active",
      },
    });
    if (!accountRes.ok()) throw new Error(`create_account_failed:${accountRes.status()}`);
    const account = (await accountRes.json()) as { id: string };

    fixtures.push({
      agencyId: agency.id,
      agencyName,
      clientId: client.id,
      clientName,
      accountId: account.id,
      accountName,
    });
  }

  return { token: await issueToken(request, user.id), fixtures };
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
