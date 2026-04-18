"use client";

import { useCallback, useEffect, useState } from "react";
import { SessionContext } from "../lib/types";

const SESSION_UPDATED_EVENT = "ops-session-updated";

export function useSessionContext() {
  const [context, setContext] = useState<SessionContext | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const apiBase = localStorage.getItem("ops_api_base") || process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
      const token = localStorage.getItem("ops_session_token") || "";
      if (!token) {
        setContext(null);
        return;
      }
      const res = await fetch(`${apiBase.replace(/\/$/, "")}/auth/internal/facade/sessions/context`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ token }),
      });
      if (!res.ok) {
        setContext(null);
        return;
      }
      const body = (await res.json()) as SessionContext;
      setContext(body);
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
