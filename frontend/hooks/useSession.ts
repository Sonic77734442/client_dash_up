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
  const [session, setSession] = useState<SessionState>({
    apiBase: defaultApiBase,
    token: "",
  });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const apiBase = localStorage.getItem(LS_API_BASE) || defaultApiBase;
    // Keep token even when token-login UI is hidden:
    // cross-domain cookie auth may be blocked and Bearer fallback is required.
    const token = localStorage.getItem(LS_SESSION_TOKEN) || "";
    setSession({ apiBase, token });
    setReady(true);
  }, [defaultApiBase]);

  const persist = useCallback((next: SessionState) => {
    localStorage.setItem(LS_API_BASE, next.apiBase);
    localStorage.setItem(LS_SESSION_TOKEN, next.token);
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    setSession(next);
  }, []);

  return { session, setSession, persist, ready };
}
