"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthMeResponse } from "../lib/types";

const LS_API_BASE = "ops_api_base";
const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

export function useAuth(defaultApiBase: string) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [role, setRole] = useState<"admin" | "agency" | "client" | null>(null);
  const [me, setMe] = useState<AuthMeResponse | null>(null);

  const refresh = useCallback(async () => {
    const apiBase = (localStorage.getItem(LS_API_BASE) || defaultApiBase).replace(/\/$/, "");
    const token = localStorage.getItem(LS_SESSION_TOKEN) || "";
    if (!token) {
      setAuthenticated(false);
      setRole(null);
      setMe(null);
      setReady(true);
      return;
    }

    try {
      const headers: HeadersInit = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`${apiBase}/auth/me`, {
        headers,
        credentials: "include",
      });
      if (!res.ok) {
        setAuthenticated(false);
        setRole(null);
        setMe(null);
        setReady(true);
        return;
      }
      const body = (await res.json()) as AuthMeResponse;
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
    const apiBase = (localStorage.getItem(LS_API_BASE) || defaultApiBase).replace(/\/$/, "");
    const token = localStorage.getItem(LS_SESSION_TOKEN) || "";
    if (token) {
      try {
        await fetch(`${apiBase}/auth/logout`, {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          credentials: "include",
        });
      } catch {
        // noop
      }
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
