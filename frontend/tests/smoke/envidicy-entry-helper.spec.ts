import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { GET } from "../../app/auth/envidicy/route";
import { beginEnvidicyAutoLogin, canManageEnvidicyBudgets, clearEnvidicyAutoLogin, ENVIDICY_AUTO_LOGIN_KEY, envidicyAccessIssue, envidicyLoginEnabled, envidicyLoginPath, envidicyLoginPolicy, shouldRedirectToMy, suppressEnvidicyAutoLogin } from "../../lib/envidicyAuth";
import type { SessionContext } from "../../lib/types";
import { oauthErrorMessage } from "../../lib/oauthError";

test("pilot admission denial is explained without promising automatic account migration", () => {
  expect(oauthErrorMessage("envidicy_pilot_only")).toBe(
    "Вход через Envidicy ID пока доступен только тестовой группе. Ваша прежняя учётная запись не изменена.",
  );
});

test("Envidicy entry only forwards a local destination, never My authority or tokens", () => {
  const request = new NextRequest("https://dash.envidicy.kz/auth/envidicy?next=%2Fportal%2Freports&project_id=forged&organization_id=forged&token=secret&redirect_uri=https://evil.test");
  const response = GET(request);
  expect(response.status).toBe(303);
  expect(response.headers.get("Location")).toBe("/api/backend/auth/envidicy/start?next=%2Fportal%2Freports");
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  for (const unsafe of ["https://evil.test", "//evil.test", "/\\evil.test", "/auth", "/api", "/auth/envidicy", "/api/backend/auth/envidicy/start", "/login/success", "/register", "/register/nested",
    "/%72egister", "/%2561uth", "/portal/../register", "/portal/%2e%2e/auth", "/portal/%252e%252e/api", "/./login", "/x/../auth/envidicy", "/%252fevil.test", "/portal%250a"] ) {
    expect(envidicyLoginPath(unsafe)).toBe("/api/backend/auth/envidicy/start?next=%2Fportal");
  }
  expect(envidicyLoginPath()).toBe("/api/backend/auth/envidicy/start?next=%2Fportal");
  expect(new URL(envidicyLoginPath("/portal/reports?period=30#spend"), "https://dash.test").searchParams.get("next"))
    .toBe("/portal/reports?period=30#spend");
});

test("optional ID entry is enabled only by the exact local backend contract", () => {
  expect(envidicyLoginEnabled({ enabled: true, login_url: "/api/backend/auth/envidicy/start" })).toBe(true);
  for (const value of [null, {}, { enabled: false }, { enabled: "true" }, { enabled: true, login_url: "https://evil.test" }]) {
    expect(envidicyLoginEnabled(value)).toBe(false);
  }
});

test("login policy preserves old backends but never treats a policy outage as legacy access", () => {
  const config = { enabled: true, login_url: "/api/backend/auth/envidicy/start" };
  expect(envidicyLoginPolicy(config)).toEqual({ localAuthEnabled: true, idEnabled: true, autoLogin: false });
  expect(envidicyLoginPolicy({ enabled: false })).toEqual({ localAuthEnabled: true, idEnabled: false, autoLogin: false });
  expect(envidicyLoginPolicy({ ...config, local_auth_enabled: true })).toEqual({ localAuthEnabled: true, idEnabled: true, autoLogin: false });
  expect(envidicyLoginPolicy({ ...config, local_auth_enabled: false })).toEqual({ localAuthEnabled: false, idEnabled: true, autoLogin: false });
  expect(envidicyLoginPolicy({ enabled: false, local_auth_enabled: false })).toEqual({ localAuthEnabled: false, idEnabled: false, autoLogin: false });
  expect(envidicyLoginPolicy({ ...config, login_url: "https://evil.test", local_auth_enabled: false, auto_login: true })).toEqual({ localAuthEnabled: false, idEnabled: false, autoLogin: false });
  expect(envidicyLoginPolicy(null, 404)).toEqual({ localAuthEnabled: true, idEnabled: false, autoLogin: false });
  for (const local_auth_enabled of [true, false]) {
    expect(envidicyLoginPolicy({ ...config, local_auth_enabled, auto_login: true }))
      .toEqual({ localAuthEnabled: local_auth_enabled, idEnabled: true, autoLogin: true });
  }
  for (const status of [401, 403, 429, 500, 502, 503]) expect(envidicyLoginPolicy(config, status)).toBeNull();
  for (const invalid of [null, [], {}, { ...config, local_auth_enabled: "false" }, { ...config, local_auth_enabled: null }, { ...config, auto_login: "true" }, { ...config, auto_login: null }]) {
    expect(envidicyLoginPolicy(invalid)).toBeNull();
  }
});

test("auto login is one-shot per tab, survives logout and fails closed without storage", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  };
  expect(beginEnvidicyAutoLogin(storage)).toBe(true);
  expect([...values.entries()]).toEqual([[ENVIDICY_AUTO_LOGIN_KEY, "attempted"]]);
  expect(beginEnvidicyAutoLogin(storage)).toBe(false);
  clearEnvidicyAutoLogin(storage);
  expect(beginEnvidicyAutoLogin(storage)).toBe(true);
  suppressEnvidicyAutoLogin(storage);
  expect(beginEnvidicyAutoLogin(storage)).toBe(false);
  expect(beginEnvidicyAutoLogin(null)).toBe(false);
  const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } };
  expect(beginEnvidicyAutoLogin(throwing)).toBe(false);
  expect(() => clearEnvidicyAutoLogin(throwing)).not.toThrow();
  expect(() => suppressEnvidicyAutoLogin(throwing)).not.toThrow();
  clearEnvidicyAutoLogin(storage);
  expect(beginEnvidicyAutoLogin({ ...storage, setItem: () => {} })).toBe(false);
});

test("My handoff requires a valid ID session and an explicit verified denial marker", () => {
  const session: SessionContext = { valid: true, role: "client", global_access: false, accessible_client_ids: [], auth_source: "envidicy_id", authority: {
    access_state: "not_granted", product: "dash.analytics", organization_id: null, project_id: null,
    permissions: [], redirect_to_my: true, my_url: "https://evil.test",
  } };
  expect(shouldRedirectToMy(session)).toBe(true);
  expect(shouldRedirectToMy({ ...session, valid: false })).toBe(false);
  expect(shouldRedirectToMy({ ...session, auth_source: "legacy" })).toBe(false);
  for (const access_state of ["ready", "context_unavailable", "project_unlinked"] as const) {
    expect(shouldRedirectToMy({ ...session, authority: { ...session.authority, access_state } })).toBe(false);
  }
  for (const redirect_to_my of [undefined, false, "true"]) {
    expect(shouldRedirectToMy({ ...session, authority: { ...session.authority, redirect_to_my } } as SessionContext)).toBe(false);
  }
  expect(shouldRedirectToMy({ ...session, authority: { ...session.authority, product: "other" } } as unknown as SessionContext)).toBe(false);
});

test("ID authority gate is additive and defaults to unavailable for incomplete context", () => {
  const legacy: SessionContext = { valid: true, role: "client", global_access: false, accessible_client_ids: [] };
  expect(envidicyAccessIssue(legacy)).toBeNull();
  expect(envidicyAccessIssue({ ...legacy, auth_source: "envidicy_id" })).toBe("context_unavailable");
  for (const access_state of ["ready", "not_granted", "project_unlinked", "context_unavailable"] as const) {
    expect(envidicyAccessIssue({ ...legacy, auth_source: "envidicy_id", authority: {
      access_state, product: "dash.analytics", organization_id: null, project_id: null, permissions: [],
    } })).toBe(access_state === "ready" ? null : access_state);
  }
  const authority = { access_state: "ready" as const, product: "dash.analytics" as const, organization_id: "org", project_id: "project", permissions: ["dash.analytics.manage"] };
  expect(canManageEnvidicyBudgets({ ...legacy, auth_source: "envidicy_id", authority })).toBe(true);
  expect(canManageEnvidicyBudgets({ ...legacy, authority })).toBe(false);
  expect(canManageEnvidicyBudgets({ ...legacy, auth_source: "envidicy_id", authority: { ...authority, access_state: "not_granted" } })).toBe(false);
  expect(canManageEnvidicyBudgets({ ...legacy, auth_source: "envidicy_id", authority: { ...authority, permissions: ["dash.analytics", "dash.analytics.read"] } })).toBe(false);
});
