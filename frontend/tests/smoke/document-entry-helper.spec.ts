import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { middleware } from "../../middleware";
import { documentEntryDecision } from "../../lib/documentEntry";
import { ENVIDICY_MY_URL, safeEnvidicyReturnPath } from "../../lib/envidicyAuth";

const policy = {
  enabled: true, auto_login: true, local_auth_enabled: true,
  server_entry_ready: true, session_cookie_name: "test_session",
  login_url: "/api/backend/auth/envidicy/start",
};
const validSession = { session: { valid: true, role: "client", auth_source: "envidicy_id" } };
function harness(options: { config?: unknown; configStatus?: number; me?: unknown; meStatus?: number } = {}) {
  const calls: Array<{ url: URL; init: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    calls.push({ url, init: init || {} });
    const config = url.pathname.endsWith("/auth/envidicy/config");
    const status = config ? options.configStatus ?? 200 : options.meStatus ?? 401;
    const body = config ? options.config ?? policy : options.me ?? {};
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  };
  return { calls, fetcher };
}

test("server entry returns a 303 through the fixed launch route, preserving path and query without JS", async () => {
  const mock = harness();
  const previousFetch = globalThis.fetch;
  const previousBase = process.env.API_UPSTREAM_BASE;
  globalThis.fetch = mock.fetcher;
  process.env.API_UPSTREAM_BASE = "https://trusted-api.test/base";
  try {
    const response = await middleware(new NextRequest("https://dash.envidicy.kz/portal/reports?range=30&tag=a%2Fb&tag=x+y"));
    expect(response.status).toBe(303);
    const location = new URL(response.headers.get("location")!);
    expect(location.origin).toBe("https://dash.envidicy.kz");
    expect(location.pathname).toBe("/auth/envidicy");
    expect(location.searchParams.get("next")).toBe("/portal/reports?range=30&tag=a%2Fb&tag=x+y");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(mock.calls.map((call) => call.url.href)).toEqual([
      "https://trusted-api.test/base/auth/envidicy/config", "https://trusted-api.test/base/auth/me",
    ]);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete process.env.API_UPSTREAM_BASE;
    else process.env.API_UPSTREAM_BASE = previousBase;
  }
});

for (const path of ["/", "/clients", "/client/test-client", "/accounts", "/budgets", "/agency/actions", "/agency/reports", "/agency/team", "/traffic", "/integrations", "/sync-monitor", "/platform", "/platform/users", "/platform/settings", "/platform/alerts", "/platform/access", "/platform/agencies", "/platform/audit", "/portal", "/portal/reports", "/portal/billing", "/portal/advertising", "/portal/plan", "/portal/changes", "/portal/leads"]) {
  test(`known protected document ${path} checks actual authentication`, async () => {
    const mock = harness();
    expect(await documentEntryDecision(new Request(`https://dash.test${path}?x=1`), "https://api.test", mock.fetcher))
      .toEqual({ kind: "redirect", location: `/auth/envidicy?${new URLSearchParams({ next: `${path}?x=1` })}` });
    expect(mock.calls).toHaveLength(2);
  });
}

test("HEAD follows the same entry decision as GET without calling the OIDC start endpoint", async () => {
  const mock = harness();
  expect((await documentEntryDecision(new Request("https://dash.test/budgets", { method: "HEAD" }), "https://api.test", mock.fetcher)).kind).toBe("redirect");
  expect(mock.calls.every((call) => !call.url.pathname.endsWith("/start"))).toBe(true);
});

test("assets, API, terminal callbacks, unknown routes, mutations and RSC/prefetch do not start document auth", async () => {
  const requests = [
    ...["/api/backend/clients", "/api/connect/start", "/auth/envidicy", "/auth/envidicy/callback", "/login/success?next=%2Fportal", "/favicon.ico", "/_next/static/a.js", "/unknown", "/login?oauth_error=", "/login?logged_out=1"]
      .map((path) => new Request(`https://dash.test${path}`)),
    new Request("https://dash.test/portal", { method: "POST" }),
    new Request("https://dash.test/portal", { headers: { RSC: "1" } }),
    new Request("https://dash.test/portal", { headers: { "Next-Router-Prefetch": "1" } }),
    new Request("https://dash.test/portal", { headers: { Purpose: "prefetch" } }),
    new Request("https://dash.test/portal", { headers: { "Sec-Purpose": "prefetch;prerender" } }),
  ];
  const mock = harness();
  for (const request of requests) expect(await documentEntryDecision(request, "https://api.test", mock.fetcher)).toEqual({ kind: "continue" });
  expect(mock.calls).toHaveLength(0);
});

test("only the configured session cookie and explicit auth hints reach the trusted API", async () => {
  const mock = harness({ me: validSession, meStatus: 200 });
  const request = new Request("https://untrusted-host.test/portal", { headers: {
    Cookie: "tracking=no; test_session=test-value; ops_session=wrong-cookie; ops_csrf=not-needed",
    Authorization: "Bearer test-document-token", "X-Session-Token": "test-header-token", "X-Forwarded-Host": "evil.test",
  } });
  expect(await documentEntryDecision(request, "https://api.test/base?ignored=true", mock.fetcher)).toEqual({ kind: "continue" });
  expect(new Headers(mock.calls[0].init.headers).has("cookie")).toBe(false);
  const headers = new Headers(mock.calls[1].init.headers);
  expect(headers.get("cookie")).toBe("test_session=test-value");
  expect(headers.get("authorization")).toBe("Bearer test-document-token");
  expect(headers.get("x-session-token")).toBe("test-header-token");
  expect(headers.has("x-forwarded-host")).toBe(false);
  for (const call of mock.calls) {
    expect(call.url.origin).toBe("https://api.test");
    expect(call.init.cache).toBe("no-store");
    expect(call.init.redirect).toBe("manual");
    expect(call.init.signal).toBeTruthy();
  }
});

test("old backend, old capability and disabled rollout preserve the existing client flow", async () => {
  for (const config of [undefined, { enabled: false }, { ...policy, server_entry_ready: false }, { ...policy, auto_login: false }]) {
    const mock = config === undefined ? harness({ configStatus: 404 }) : harness({ config });
    expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", mock.fetcher)).toEqual({ kind: "continue" });
    expect(mock.calls).toHaveLength(1);
  }
  const withoutCapability = { ...policy };
  delete (withoutCapability as Partial<typeof policy>).server_entry_ready;
  const mock = harness({ config: withoutCapability });
  expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", mock.fetcher)).toEqual({ kind: "continue" });
  expect(mock.calls).toHaveLength(1);
});

test("ID-only closes direct login/register including legacy opt-out; transition retains local entry", async () => {
  for (const path of ["/login", "/register"]) {
    const url = `https://dash.test${path}?legacy=1&next=%2Fportal%2Freports%3Fperiod%3D30`;
    const closed = harness({ config: { ...policy, auto_login: false, local_auth_enabled: false } });
    expect(await documentEntryDecision(new Request(url), "https://api.test", closed.fetcher))
      .toEqual({ kind: "redirect", location: "/auth/envidicy?next=%2Fportal%2Freports%3Fperiod%3D30" });
    const transition = harness();
    expect(await documentEntryDecision(new Request(url), "https://api.test", transition.fetcher)).toEqual({ kind: "continue" });
    expect(transition.calls).toHaveLength(1);
  }
});

test("an expired or forged session cookie still requires a confirmed 401 before ID entry", async () => {
  const mock = harness();
  expect((await documentEntryDecision(new Request("https://dash.test/portal", { headers: { Cookie: "test_session=forged" } }), "https://api.test", mock.fetcher)).kind).toBe("redirect");
  expect(mock.calls).toHaveLength(2);
});

test("only verified ID My denial redirects to the fixed My URL, never unlinked/outage or payload URLs", async () => {
  for (const state of ["ready", "not_granted", "project_unlinked", "context_unavailable"]) {
    const me = { session: { ...validSession.session, authority: {
      access_state: state, redirect_to_my: true, product: "dash.analytics", my_url: "https://evil.test",
    } } };
    const mock = harness({ me, meStatus: 200 });
    expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", mock.fetcher))
      .toEqual(state === "not_granted" ? { kind: "redirect", location: ENVIDICY_MY_URL } : { kind: "continue" });
  }
});

test("legacy sessions remain accepted during transition but never satisfy ID-only document entry", async () => {
  for (const source of ["legacy", undefined]) {
    const me = { session: { ...validSession.session, auth_source: source } };
    const transition = harness({ me, meStatus: 200 });
    expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", transition.fetcher)).toEqual({ kind: "continue" });
    const closed = harness({ config: { ...policy, local_auth_enabled: false }, me, meStatus: 200 });
    expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", closed.fetcher)).toEqual({ kind: "unavailable" });
  }
});

test("policy/transport failures, redirects and non-401 auth errors fail closed without OIDC", async () => {
  for (const options of [
    { configStatus: 503 }, { configStatus: 302 }, { meStatus: 403 }, { meStatus: 429 }, { meStatus: 503 }, { meStatus: 302 },
    { meStatus: 200, me: { session: { valid: false, role: "client" } } },
    { meStatus: 200, me: { session: { valid: true, role: "unknown" } } },
    { config: { ...policy, server_entry_ready: "true" } },
    { config: { ...policy, session_cookie_name: "cookie;bad" } },
    { config: { ...policy, session_cookie_name: "x".repeat(129) } },
    { config: { ...policy, session_cookie_name: undefined } },
    { config: { ...policy, auto_login: "true" } },
    { config: { ...policy, enabled: false, local_auth_enabled: false } },
    { config: { ...policy, login_url: "https://evil.test" } },
  ]) {
    const mock = harness(options);
    expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", mock.fetcher)).toEqual({ kind: "unavailable" });
    expect(mock.calls.length).toBeLessThanOrEqual(2);
  }
  const unavailable: typeof fetch = async () => { throw new Error("transport unavailable"); };
  expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", unavailable)).toEqual({ kind: "unavailable" });
});

test("invalid upstream never falls back to the request host", async () => {
  const mock = harness();
  for (const base of ["/api/backend", "ftp://api.test", "https://user:secret@api.test"]) {
    expect(await documentEntryDecision(new Request("https://evil.test/portal"), base, mock.fetcher)).toEqual({ kind: "unavailable" });
  }
  expect(mock.calls).toHaveLength(0);
});

test("an upstream that never responds is bounded and cannot trigger automatic OIDC", async () => {
  const stalled: typeof fetch = async (_input, init) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  });
  expect(await documentEntryDecision(new Request("https://dash.test/portal"), "https://api.test", stalled)).toEqual({ kind: "unavailable" });
});

test("completion and server launch share strict auth-loop return validation", () => {
  for (const next of ["/login/success?next=%2Fportal", "/%6cogin/success", "/portal/../register", "/portal/%2e%2e/auth/envidicy", "/%2561uth", "/api", "//evil.test", "https://evil.test", "/\\evil.test", "/portal%0a"])
    expect(safeEnvidicyReturnPath(next)).toBe("/portal");
  expect(safeEnvidicyReturnPath("/portal/reports?period=30&tag=a%2Fb#history")).toBe("/portal/reports?period=30&tag=a%2Fb#history");
});
