import { safeRelativePath } from "./authRedirect";
import type { SessionContext } from "./types";

export const ENVIDICY_START_PATH = "/api/backend/auth/envidicy/start";
export const ENVIDICY_MY_URL = "https://my.envidicy.com/products";

export function envidicyLoginPath(next?: string | null): string {
  let destination = safeRelativePath(next, "/portal");
  const pathname = new URL(destination, "https://dash.envidicy.local").pathname;
  if (pathname === "/login" || pathname.startsWith("/login/")
    || pathname === "/auth" || pathname.startsWith("/auth/")
    || pathname === "/api" || pathname.startsWith("/api/")) destination = "/portal";
  return `${ENVIDICY_START_PATH}?${new URLSearchParams({ next: destination })}`;
}

export function envidicyLoginEnabled(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const config = payload as { enabled?: unknown; login_url?: unknown };
  return config.enabled === true && config.login_url === ENVIDICY_START_PATH;
}

export type EnvidicyLoginPolicy = { localAuthEnabled: boolean; idEnabled: boolean };

export function envidicyLoginPolicy(payload: unknown, status = 200): EnvidicyLoginPolicy | null {
  // A pre-integration backend has no config endpoint. Other failures cannot
  // establish whether ordinary local login has already been closed.
  if (status === 404) return { localAuthEnabled: true, idEnabled: false };
  if (status !== 200 || !payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const config = payload as { enabled?: unknown; local_auth_enabled?: unknown };
  if (typeof config.enabled !== "boolean"
    || (config.local_auth_enabled !== undefined && typeof config.local_auth_enabled !== "boolean")) return null;
  return {
    localAuthEnabled: config.local_auth_enabled !== false,
    idEnabled: envidicyLoginEnabled(payload),
  };
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
