import { isAppRole } from "./authRedirect";
import { envidicyLoginPath, envidicyLoginPolicy, ENVIDICY_MY_URL, shouldRedirectToMy } from "./envidicyAuth";
import type { SessionContext } from "./types";

const PROTECTED_DOCUMENTS = new Set([
  "/", "/accounts", "/budgets", "/clients", "/agency/actions", "/agency/reports", "/agency/team",
  "/traffic", "/integrations", "/sync-monitor", "/platform", "/platform/users", "/platform/settings",
  "/platform/alerts", "/platform/access", "/platform/agencies", "/platform/audit", "/portal",
  "/portal/reports", "/portal/billing", "/portal/advertising", "/portal/plan", "/portal/changes", "/portal/leads",
]);
const REQUEST_TIMEOUT_MS = 5_000;
const COOKIE_NAME = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}$/;

export type DocumentEntryDecision =
  | { kind: "continue" }
  | { kind: "redirect"; location: string }
  | { kind: "unavailable" };

function routeKind(request: Request): "protected" | "login" | null {
  // API, asset, prefetch and RSC requests must never create an OIDC transaction.
  if (!["GET", "HEAD"].includes(request.method) || request.headers.get("rsc") === "1"
    || request.headers.has("next-router-prefetch") || request.headers.get("purpose") === "prefetch"
    || request.headers.get("sec-purpose")?.includes("prefetch")) return null;
  const url = new URL(request.url);
  const pathname = url.pathname.replace(/\/$/, "") || "/";
  if (pathname === "/login" || pathname === "/register") {
    if (pathname === "/login" && (url.searchParams.has("oauth_error") || url.searchParams.get("logged_out") === "1")) return null;
    return "login";
  }
  return PROTECTED_DOCUMENTS.has(pathname) || /^\/client\/[^/]+$/.test(pathname) ? "protected" : null;
}

function upstreamUrl(base: string, path: string): URL {
  const url = new URL(base);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Invalid upstream");
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/${path}`;
  url.search = "";
  url.hash = "";
  return url;
}

function sessionHeaders(request: Request, cookieName: string): Headers {
  const headers = new Headers({ Accept: "application/json" });
  const cookies = (request.headers.get("cookie") || "").split(";")
    .map((part) => part.trim()).filter((part) => part.indexOf("=") > 0 && part.slice(0, part.indexOf("=")) === cookieName);
  if (cookies.length) headers.set("Cookie", cookies.join("; "));
  for (const name of ["Authorization", "X-Session-Token"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

async function fetchJson(url: URL, headers: Headers, fetcher: typeof fetch) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetcher(url, { headers, cache: "no-store", redirect: "manual", signal: controller.signal });
    const body: unknown = response.status === 200 ? await response.json() : null;
    return { status: response.status, body };
  } finally { clearTimeout(timeout); }
}

/** Server document entry only. Backend authorization remains authoritative for every data request. */
export async function documentEntryDecision(request: Request, upstreamBase: string, fetcher: typeof fetch = fetch): Promise<DocumentEntryDecision> {
  const kind = routeKind(request);
  if (!kind) return { kind: "continue" };
  try {
    // Never resolve the API from Host, a return URL, forwarded headers or browser storage.
    const config = await fetchJson(upstreamUrl(upstreamBase, "auth/envidicy/config"), new Headers({ Accept: "application/json" }), fetcher);
    const policy = envidicyLoginPolicy(config.body, config.status);
    if (!policy) return { kind: "unavailable" };
    const settings = config.body as { server_entry_ready?: unknown; session_cookie_name?: unknown; auto_login?: unknown } | null;
    if (settings?.server_entry_ready !== undefined && typeof settings.server_entry_ready !== "boolean") return { kind: "unavailable" };
    // Old callbacks return directly to protected documents: activating here would loop if cookies are rejected.
    if (settings?.server_entry_ready !== true) return { kind: "continue" };
    if (settings.auto_login === true && !policy.idEnabled) return { kind: "unavailable" };
    if (kind === "login" && policy.localAuthEnabled) return { kind: "continue" };
    if (!policy.autoLogin && policy.localAuthEnabled) return { kind: "continue" };
    if (!policy.idEnabled || typeof settings.session_cookie_name !== "string" || !COOKIE_NAME.test(settings.session_cookie_name)) return { kind: "unavailable" };

    const me = await fetchJson(upstreamUrl(upstreamBase, "auth/me"), sessionHeaders(request, settings.session_cookie_name), fetcher);
    if (me.status === 200) {
      const session = (me.body as { session?: SessionContext } | null)?.session;
      if (session?.valid !== true || !isAppRole(session.role)) return { kind: "unavailable" };
      if (!policy.localAuthEnabled && session.auth_source !== "envidicy_id") return { kind: "unavailable" };
      return shouldRedirectToMy(session) ? { kind: "redirect", location: ENVIDICY_MY_URL } : { kind: "continue" };
    }
    if (me.status !== 401) return { kind: "unavailable" };
    const url = new URL(request.url);
    const next = kind === "login" ? url.searchParams.get("next") : `${url.pathname}${url.search}`;
    const start = new URL(envidicyLoginPath(next), "https://dash.envidicy.local");
    return { kind: "redirect", location: `/auth/envidicy?${start.searchParams}` };
  } catch {
    // No credentials, upstream bodies or request URLs are logged on failures.
    return { kind: "unavailable" };
  }
}
