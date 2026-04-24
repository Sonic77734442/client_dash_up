"use client";

import { useCallback, useEffect, useState } from "react";

const LS_API_BASE = "ops_api_base";
const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

type SessionState = {
  apiBase: string;
  token: string;
};

export function useSession(defaultApiBase: string) {
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const [session, setSession] = useState<SessionState>({
    apiBase: defaultApiBase,
    token: "",
  });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const apiBase = localStorage.getItem(LS_API_BASE) || defaultApiBase;
    const token = tokenLoginEnabled ? (localStorage.getItem(LS_SESSION_TOKEN) || "") : "";
    if (!tokenLoginEnabled) {
      localStorage.removeItem(LS_SESSION_TOKEN);
    }
    setSession({ apiBase, token });
    setReady(true);
  }, [defaultApiBase, tokenLoginEnabled]);

  const persist = useCallback((next: SessionState) => {
    localStorage.setItem(LS_API_BASE, next.apiBase);
    localStorage.setItem(LS_SESSION_TOKEN, next.token);
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    setSession(next);
  }, []);

  return { session, setSession, persist, ready };
}
