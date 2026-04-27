"use client";

import { useCallback, useEffect, useState } from "react";
import { normalizeApiBase, resolveApiBase } from "../lib/apiBase";

const LS_API_BASE = "ops_api_base";
const LS_SESSION_TOKEN = "ops_session_token";
const SESSION_UPDATED_EVENT = "ops-session-updated";

type SessionState = {
  apiBase: string;
  token: string;
};

export function useSession(defaultApiBase: string) {
  const [session, setSession] = useState<SessionState>({
    apiBase: normalizeApiBase(defaultApiBase, "http://localhost:8000"),
    token: "",
  });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const apiBase = resolveApiBase(defaultApiBase);
    // Keep token even when token-login UI is hidden:
    // cross-domain cookie auth may be blocked and Bearer fallback is required.
    const token = localStorage.getItem(LS_SESSION_TOKEN) || "";
    setSession({ apiBase, token });
    setReady(true);
  }, [defaultApiBase]);

  const persist = useCallback((next: SessionState) => {
    localStorage.setItem(LS_API_BASE, normalizeApiBase(next.apiBase, defaultApiBase));
    localStorage.setItem(LS_SESSION_TOKEN, next.token);
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    setSession((s) => ({ ...s, apiBase: normalizeApiBase(next.apiBase, defaultApiBase), token: next.token }));
  }, [defaultApiBase]);

  return { session, setSession, persist, ready };
}
