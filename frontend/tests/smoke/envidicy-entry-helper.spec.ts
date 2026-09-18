import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { GET } from "../../app/auth/envidicy/route";
import { canManageEnvidicyBudgets, envidicyAccessIssue, envidicyLoginEnabled, envidicyLoginPath, envidicyLoginPolicy } from "../../lib/envidicyAuth";
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
  for (const unsafe of ["https://evil.test", "//evil.test", "/\\evil.test", "/auth/envidicy", "/api/backend/auth/envidicy/start", "/login/success"]) {
    expect(envidicyLoginPath(unsafe)).toBe("/api/backend/auth/envidicy/start?next=%2Fportal");
  }
  expect(envidicyLoginPath()).toBe("/api/backend/auth/envidicy/start?next=%2Fportal");
});

test("optional ID entry is enabled only by the exact local backend contract", () => {
  expect(envidicyLoginEnabled({ enabled: true, login_url: "/api/backend/auth/envidicy/start" })).toBe(true);
  for (const value of [null, {}, { enabled: false }, { enabled: "true" }, { enabled: true, login_url: "https://evil.test" }]) {
    expect(envidicyLoginEnabled(value)).toBe(false);
  }
});

test("login policy preserves old backends but never treats a policy outage as legacy access", () => {
  const config = { enabled: true, login_url: "/api/backend/auth/envidicy/start" };
  expect(envidicyLoginPolicy(config)).toEqual({ localAuthEnabled: true, idEnabled: true });
  expect(envidicyLoginPolicy({ enabled: false })).toEqual({ localAuthEnabled: true, idEnabled: false });
  expect(envidicyLoginPolicy({ ...config, local_auth_enabled: true })).toEqual({ localAuthEnabled: true, idEnabled: true });
  expect(envidicyLoginPolicy({ ...config, local_auth_enabled: false })).toEqual({ localAuthEnabled: false, idEnabled: true });
  expect(envidicyLoginPolicy({ enabled: false, local_auth_enabled: false })).toEqual({ localAuthEnabled: false, idEnabled: false });
  expect(envidicyLoginPolicy({ ...config, login_url: "https://evil.test", local_auth_enabled: false })).toEqual({ localAuthEnabled: false, idEnabled: false });
  expect(envidicyLoginPolicy(null, 404)).toEqual({ localAuthEnabled: true, idEnabled: false });
  for (const status of [401, 403, 429, 500, 502, 503]) expect(envidicyLoginPolicy(config, status)).toBeNull();
  for (const invalid of [null, [], {}, { ...config, local_auth_enabled: "false" }, { ...config, local_auth_enabled: null }]) {
    expect(envidicyLoginPolicy(invalid)).toBeNull();
  }
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
