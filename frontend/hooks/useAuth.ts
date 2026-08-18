"use client";

import { useCallback } from "react";
import { fetchJson } from "../lib/api";
import { resolveApiBase } from "../lib/apiBase";
import { isAppRole } from "../lib/authRedirect";
import { clearSessionToken } from "../lib/sessionToken";
import { useSessionContext } from "./useSessionContext";

const SESSION_UPDATED_EVENT = "ops-session-updated";

export function useAuth(defaultApiBase: string) {
  const { context, me, loading, error, refresh } = useSessionContext();
  const role = isAppRole(context?.role) ? context.role : null;
  const authenticated = Boolean(context?.valid && role);
  const ready = !loading;

  const logout = useCallback(async () => {
    const apiBase = resolveApiBase(defaultApiBase);
    try {
      await fetchJson<{ status: string }>(apiBase, "/auth/logout", "", {
        method: "POST",
      });
    } catch {
      // noop
    }
    clearSessionToken();
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
  }, [defaultApiBase]);

  return { ready, authenticated, role, me, error, refresh, logout };
}
