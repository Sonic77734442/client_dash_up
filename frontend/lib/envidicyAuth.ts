import { safeRelativePath } from "./authRedirect";
import type { SessionContext } from "./types";

export const ENVIDICY_START_PATH = "/api/backend/auth/envidicy/start";
export const ENVIDICY_MY_URL = "https://my.envidicy.com/products";

export function envidicyLoginPath(next?: string | null): string {
  let destination = safeRelativePath(next, "/portal");
  try {
    let decoded = destination;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const value = decodeURIComponent(decoded);
      if (value === decoded) break;
      decoded = value;
    }
    const checked = new URL(decoded, "https://dash.envidicy.local");
    const pathname = checked.pathname;
    if (destination.length > 1024 || decodeURIComponent(decoded) !== decoded
      || decoded.startsWith("//") || decoded.includes("\\") || /[\u0000-\u001f\u007f]/.test(decoded)
      || checked.origin !== "https://dash.envidicy.local"
      || pathname.startsWith("/login")
      || pathname === "/register" || pathname.startsWith("/register/")
      || pathname === "/auth" || pathname.startsWith("/auth/")
      || pathname === "/api" || pathname.startsWith("/api/")) destination = "/portal";
  } catch { destination = "/portal"; }
  return `${ENVIDICY_START_PATH}?${new URLSearchParams({ next: destination })}`;
}

export function envidicyLoginEnabled(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const config = payload as { enabled?: unknown; login_url?: unknown };
  return config.enabled === true && config.login_url === ENVIDICY_START_PATH;
}

export type EnvidicyLoginPolicy = { localAuthEnabled: boolean; idEnabled: boolean; autoLogin: boolean };

export function envidicyLoginPolicy(payload: unknown, status = 200): EnvidicyLoginPolicy | null {
  // A pre-integration backend has no config endpoint. Other failures cannot
  // establish whether ordinary local login has already been closed.
  if (status === 404) return { localAuthEnabled: true, idEnabled: false, autoLogin: false };
  if (status !== 200 || !payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const config = payload as { enabled?: unknown; local_auth_enabled?: unknown; auto_login?: unknown };
  if (typeof config.enabled !== "boolean"
    || (config.local_auth_enabled !== undefined && typeof config.local_auth_enabled !== "boolean")
    || (config.auto_login !== undefined && typeof config.auto_login !== "boolean")) return null;
  return {
    localAuthEnabled: config.local_auth_enabled !== false,
    idEnabled: envidicyLoginEnabled(payload),
    autoLogin: config.auto_login === true && envidicyLoginEnabled(payload),
  };
}

// Per-tab UX guard only: never contains identity, tokens, authority or a URL.
export const ENVIDICY_AUTO_LOGIN_KEY = "ops-envidicy-auto-login";
type LoginStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function loginStorage(): LoginStorage | null {
  try { return typeof window === "undefined" ? null : window.sessionStorage; } catch { return null; }
}

export function beginEnvidicyAutoLogin(storage: LoginStorage | null = loginStorage()): boolean {
  if (!storage) return false;
  try {
    if (storage.getItem(ENVIDICY_AUTO_LOGIN_KEY) !== null) return false;
    storage.setItem(ENVIDICY_AUTO_LOGIN_KEY, "attempted");
    return storage.getItem(ENVIDICY_AUTO_LOGIN_KEY) === "attempted";
  } catch { return false; }
}

export function clearEnvidicyAutoLogin(storage: LoginStorage | null = loginStorage()): void {
  try { storage?.removeItem(ENVIDICY_AUTO_LOGIN_KEY); } catch { /* Manual login remains available. */ }
}

export function suppressEnvidicyAutoLogin(storage: LoginStorage | null = loginStorage()): void {
  try { storage?.setItem(ENVIDICY_AUTO_LOGIN_KEY, "signed_out"); } catch { /* No storage means no auto login. */ }
}

export function shouldRedirectToMy(session?: SessionContext | null): boolean {
  return session?.valid === true && session.auth_source === "envidicy_id"
    && session.authority?.product === "dash.analytics"
    && session.authority.access_state === "not_granted"
    && session.authority.redirect_to_my === true;
}

export function envidicyAccessIssue(session?: SessionContext | null) {
  if (!session?.valid || session.auth_source !== "envidicy_id") return null;
  if (session.authority?.product !== "dash.analytics") return "context_unavailable";
  const state = session.authority?.access_state;
  if (state === "ready") return null;
  if (state === "not_granted" || state === "project_unlinked") return state;
  return "context_unavailable";
}

export function canManageEnvidicyBudgets(session?: SessionContext | null): boolean {
  return session?.valid === true && session.auth_source === "envidicy_id"
    && session.authority?.access_state === "ready"
    && session.authority.product === "dash.analytics"
    && Array.isArray(session.authority.permissions)
    && session.authority.permissions.includes("dash.analytics.manage");
}
