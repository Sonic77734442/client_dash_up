"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthMeResponse } from "../lib/types";
import { fetchJson } from "../lib/api";
import { resolveApiBase } from "../lib/apiBase";

const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

export function useAuth(defaultApiBase: string) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [role, setRole] = useState<"admin" | "agency" | "client" | null>(null);
  const [me, setMe] = useState<AuthMeResponse | null>(null);

  const refresh = useCallback(async () => {
    const apiBase = resolveApiBase(defaultApiBase);

    try {
      // Ask backend via cookie auth first; fetchJson retries with localStorage bearer token on 401.
      const body = await fetchJson<AuthMeResponse>(apiBase, "/auth/me", "");
      setMe(body);
      setRole(body.session.role || null);
      setAuthenticated(Boolean(body.session.valid));
      setReady(true);
    } catch {
      setAuthenticated(false);
      setRole(null);
      setMe(null);
      setReady(true);
    }
  }, [defaultApiBase]);

  const logout = useCallback(async () => {
    const apiBase = resolveApiBase(defaultApiBase);
    try {
      await fetchJson<{ status: string }>(apiBase, "/auth/logout", "", {
        method: "POST",
      });
    } catch {
      // noop
    }
    localStorage.removeItem(LS_SESSION_TOKEN);
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    setAuthenticated(false);
    setRole(null);
    setMe(null);
  }, [defaultApiBase]);

  useEffect(() => {
    void refresh();
    const onStorage = () => void refresh();
    const onSessionUpdated = () => void refresh();
    window.addEventListener("storage", onStorage);
    window.addEventListener(SESSION_UPDATED_EVENT, onSessionUpdated);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(SESSION_UPDATED_EVENT, onSessionUpdated);
    };
  }, [refresh]);

  return { ready, authenticated, role, me, refresh, logout };
}
