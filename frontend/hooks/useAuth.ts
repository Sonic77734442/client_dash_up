"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthMeResponse } from "../lib/types";
import { ApiRequestError, fetchJson } from "../lib/api";
import { resolveApiBase } from "../lib/apiBase";
import { isAppRole } from "../lib/authRedirect";
import { clearSessionToken, getSessionToken } from "../lib/sessionToken";

const SESSION_UPDATED_EVENT = "ops-session-updated";

export function useAuth(defaultApiBase: string) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [role, setRole] = useState<"admin" | "agency" | "client" | "solo_client" | null>(null);
  const [me, setMe] = useState<AuthMeResponse | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const apiBase = resolveApiBase(defaultApiBase);
    const token = getSessionToken();
    setReady(false);
    setError("");

    const retryDelays = [0, 500, 1_500];
    let lastError: unknown = null;
    for (let attempt = 0; attempt < retryDelays.length; attempt += 1) {
      if (retryDelays[attempt]) {
        await new Promise((resolve) => window.setTimeout(resolve, retryDelays[attempt]));
      }
      try {
        const body = await fetchJson<AuthMeResponse>(apiBase, "/auth/me", token);
        const nextRole = isAppRole(body.session.role) ? body.session.role : null;
        setMe(body);
        setRole(nextRole);
        setAuthenticated(Boolean(body.session.valid && nextRole));
        setError("");
        setReady(true);
        return;
      } catch (err) {
        if (err instanceof ApiRequestError && err.status === 401) {
          setAuthenticated(false);
          setRole(null);
          setMe(null);
          setError("");
          setReady(true);
          return;
        }
        lastError = err;
      }
    }

    const message = lastError instanceof Error ? lastError.message : "Сервис временно недоступен";
    setError(message);
    setReady(true);
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
    clearSessionToken();
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    setAuthenticated(false);
    setRole(null);
    setMe(null);
    setError("");
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

  return { ready, authenticated, role, me, error, refresh, logout };
}
