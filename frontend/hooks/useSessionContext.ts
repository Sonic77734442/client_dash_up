"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthMeResponse, SessionContext } from "../lib/types";
import { fetchJson } from "../lib/api";
import { resolveApiBase } from "../lib/apiBase";

const SESSION_UPDATED_EVENT = "ops-session-updated";
const LS_SESSION_TOKEN = "ops_session_token";

export function useSessionContext() {
  const [context, setContext] = useState<SessionContext | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const apiBase = resolveApiBase(process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000");
      const token = localStorage.getItem(LS_SESSION_TOKEN) || "";
      const body = await fetchJson<AuthMeResponse>(apiBase, "/auth/me", token);
      setContext(body.session || null);
    } catch {
      setContext(null);
    } finally {
      setLoading(false);
    }
  }, []);

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

  return { context, loading, refresh };
}
