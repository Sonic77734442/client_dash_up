import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { GET } from "../../app/api/connect/start/route";
import {
  oauthRelayLaunchPath,
  resolveOAuthRelayTarget,
} from "../../lib/oauthLaunchRelay";

test("neutral OAuth launch maps only allowlisted sources and query fields", () => {
  const target = resolveOAuthRelayTarget(
    new URL(
      "https://frontend.test/api/connect/start"
      + "?source=g&next=%2Fsync-monitor&intent=connect&connect_mode=add"
      + "&agency_id=agency-1&unknown=must-not-leave",
    ),
    "https://backend.test/root",
  );

  expect(target.origin).toBe("https://backend.test");
  expect(target.pathname).toBe("/root/auth/google/start");
  expect(target.searchParams.get("next")).toBe("/sync-monitor");
  expect(target.searchParams.get("agency_id")).toBe("agency-1");
  expect(target.searchParams.has("source")).toBeFalsy();
  expect(target.searchParams.has("unknown")).toBeFalsy();
  expect(oauthRelayLaunchPath("facebook", { next: "/integrations", intent: "connect" }))
    .toBe("/api/connect/start?next=%2Fintegrations&intent=connect&source=m");
});

test("neutral OAuth launch rejects unsupported sources and external return paths", () => {
  expect(() => resolveOAuthRelayTarget(
    new URL("https://frontend.test/api/connect/start?source=x&next=%2F"),
    "https://backend.test",
  )).toThrow("OAuth source is not supported");

  expect(() => resolveOAuthRelayTarget(
    new URL("https://frontend.test/api/connect/start?source=m&next=https%3A%2F%2Fevil.test"),
    "https://backend.test",
  )).toThrow("OAuth return path is invalid");
});

test("relay forwards auth cookies and preserves redirect plus every Set-Cookie header", async () => {
  const originalFetch = globalThis.fetch;
  const originalUpstream = process.env.API_UPSTREAM_BASE;
  let capturedRequest: { url: string; cookie: string | null; redirect?: RequestRedirect } | null = null;

  process.env.API_UPSTREAM_BASE = "https://backend.test";
  globalThis.fetch = (async (input: URL | RequestInfo, init?: RequestInit) => {
    const url = input instanceof URL ? input.toString() : String(input);
    const headers = new Headers(init?.headers);
    capturedRequest = {
      url,
      cookie: headers.get("cookie"),
      redirect: init?.redirect,
    };
    const responseHeaders = new Headers({ location: "https://accounts.google.test/authorize?state=safe" });
    responseHeaders.append("set-cookie", "oauth_nonce=one; Path=/; HttpOnly; Secure");
    responseHeaders.append("set-cookie", "oauth_guard=two; Path=/; HttpOnly; Secure");
    return new Response(null, { status: 302, headers: responseHeaders });
  }) as typeof fetch;

  try {
    const request = new NextRequest(
      "https://frontend.test/api/connect/start?source=g&next=%2Fsync-monitor&intent=connect",
      { headers: { cookie: "ops_session=session-value; ops_csrf=csrf-value" } },
    );
    const response = await GET(request);

    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe("https://accounts.google.test/authorize?state=safe");
    const cookies = (response.headers as Headers & { getSetCookie?: () => string[] }).getSetCookie?.() || [];
    expect(cookies).toEqual([
      "oauth_nonce=one; Path=/; HttpOnly; Secure",
      "oauth_guard=two; Path=/; HttpOnly; Secure",
    ]);
    expect(capturedRequest).toEqual({
      url: "https://backend.test/auth/google/start?next=%2Fsync-monitor&intent=connect",
      cookie: "ops_session=session-value; ops_csrf=csrf-value",
      redirect: "manual",
    });
  } finally {
    globalThis.fetch = originalFetch;
    if (originalUpstream === undefined) delete process.env.API_UPSTREAM_BASE;
    else process.env.API_UPSTREAM_BASE = originalUpstream;
  }
});

test("relay returns a generic error without exposing an invalid upstream value", async () => {
  const originalUpstream = process.env.API_UPSTREAM_BASE;
  process.env.API_UPSTREAM_BASE = "file:///private/provider-secret";
  try {
    const response = await GET(new NextRequest(
      "https://frontend.test/api/connect/start?source=g&intent=connect&next=%2F",
    ));
    const body = await response.json();
    expect(response.status).toBe(502);
    expect(body).toEqual({
      error: {
        code: "oauth_relay_unavailable",
        message: "OAuth is temporarily unavailable",
        details: {},
      },
    });
    expect(JSON.stringify(body)).not.toContain("provider-secret");
  } finally {
    if (originalUpstream === undefined) delete process.env.API_UPSTREAM_BASE;
    else process.env.API_UPSTREAM_BASE = originalUpstream;
  }
});
