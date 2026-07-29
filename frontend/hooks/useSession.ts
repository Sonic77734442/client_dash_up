"use client";

import { useCallback, useEffect, useState } from "react";
import { DEFAULT_API_BASE, normalizeApiBase, resolveApiBase } from "../lib/apiBase";
import { getSessionToken, setSessionToken } from "../lib/sessionToken";

const LS_API_BASE = "ops_api_base";
const SESSION_UPDATED_EVENT = "ops-session-updated";

type SessionState = {
  apiBase: string;
  token: string;
};

export function useSession(defaultApiBase: string) {
  const [session, setSession] = useState<SessionState>({
    apiBase: normalizeApiBase(defaultApiBase, DEFAULT_API_BASE),
    token: "",
  });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const apiBase = resolveApiBase(defaultApiBase);
    // Explicit debug tokens are memory-only and disappear on a full reload.
    const token = getSessionToken();
    setSession({ apiBase, token });
    setReady(true);
  }, [defaultApiBase]);

  const persist = useCallback((next: SessionState) => {
    localStorage.setItem(LS_API_BASE, normalizeApiBase(next.apiBase, defaultApiBase));
    setSessionToken(next.token);
    window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
    setSession((s) => ({ ...s, apiBase: normalizeApiBase(next.apiBase, defaultApiBase), token: next.token }));
  }, [defaultApiBase]);

  return { session, setSession, persist, ready };
}
