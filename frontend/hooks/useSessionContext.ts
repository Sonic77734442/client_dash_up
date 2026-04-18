"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthMeResponse, SessionContext } from "../lib/types";

const SESSION_UPDATED_EVENT = "ops-session-updated";

export function useSessionContext() {
  const [context, setContext] = useState<SessionContext | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const apiBase = localStorage.getItem("ops_api_base") || process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
      const token = localStorage.getItem("ops_session_token") || "";
      const headers: HeadersInit = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`${apiBase.replace(/\/$/, "")}/auth/me`, {
        method: "GET",
        headers,
        credentials: "include",
      });
      if (!res.ok) {
        setContext(null);
        return;
      }
      const body = (await res.json()) as AuthMeResponse;
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
