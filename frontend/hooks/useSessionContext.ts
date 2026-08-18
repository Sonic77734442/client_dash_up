"use client";

import { useCallback, useEffect, useState } from "react";
import { AuthMeResponse, SessionContext } from "../lib/types";
import { ApiRequestError, fetchJson } from "../lib/api";
import { resolveApiBase } from "../lib/apiBase";
import { getSessionToken } from "../lib/sessionToken";

const SESSION_UPDATED_EVENT = "ops-session-updated";
const SESSION_CACHE_TTL_MS = 5_000;

let sharedContext: SessionContext | null = null;
let sharedMe: AuthMeResponse | null = null;
let sharedError = "";
let sharedStatus: number | null = null;
let sharedResolved = false;
let sharedResolvedAt = 0;
type SharedSessionSnapshot = {
  context: SessionContext | null;
  me: AuthMeResponse | null;
  error: string;
  status: number | null;
};
let sharedRequest: Promise<SharedSessionSnapshot> | null = null;

function hasFreshSharedContext() {
  return sharedResolved && Date.now() - sharedResolvedAt < SESSION_CACHE_TTL_MS;
}

function sharedSnapshot(): SharedSessionSnapshot {
  return {
    context: sharedContext,
    me: sharedMe,
    error: sharedError,
    status: sharedStatus,
  };
}

async function requestSharedContext(force: boolean): Promise<SharedSessionSnapshot> {
  if (!force && hasFreshSharedContext()) return sharedSnapshot();
  if (sharedRequest) return sharedRequest;

  sharedRequest = (async () => {
    const apiBase = resolveApiBase(process.env.NEXT_PUBLIC_API_BASE || "/api/backend");
    const token = getSessionToken();
    const retryDelays = [0, 500, 1_500];
    let lastError: unknown = null;

    for (const delay of retryDelays) {
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      try {
        const body = await fetchJson<AuthMeResponse>(apiBase, "/auth/me", token);
        sharedMe = body;
        sharedContext = body.session || null;
        sharedError = "";
        sharedStatus = 200;
        lastError = null;
        break;
      } catch (reason) {
        lastError = reason;
        if (reason instanceof ApiRequestError && reason.status === 401) {
          sharedMe = null;
          sharedContext = null;
          sharedError = "";
          sharedStatus = 401;
          lastError = null;
          break;
        }
      }
    }

    if (lastError) {
      // Authentication and role resolution are fail-closed for every consumer.
      sharedMe = null;
      sharedContext = null;
      sharedError = lastError instanceof Error ? lastError.message : "Сервис временно недоступен";
      sharedStatus = lastError instanceof ApiRequestError ? lastError.status : null;
    }
    sharedResolved = true;
    sharedResolvedAt = Date.now();
    return sharedSnapshot();
  })().finally(() => {
    sharedRequest = null;
  });

  return sharedRequest;
}

export function useSessionContext() {
  const useCachedContext = hasFreshSharedContext();
  const [context, setContext] = useState<SessionContext | null>(
    useCachedContext ? sharedContext : null,
  );
  const [me, setMe] = useState<AuthMeResponse | null>(useCachedContext ? sharedMe : null);
  const [error, setError] = useState(useCachedContext ? sharedError : "");
  const [status, setStatus] = useState<number | null>(useCachedContext ? sharedStatus : null);
  const [loading, setLoading] = useState(!useCachedContext);

  const load = useCallback(async (force: boolean) => {
    setLoading(true);
    if (force) {
      setContext(null);
      setMe(null);
      setError("");
      setStatus(null);
    }
    const next = await requestSharedContext(force);
    setContext(next.context);
    setMe(next.me);
    setError(next.error);
    setStatus(next.status);
    setLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    await load(true);
  }, [load]);

  useEffect(() => {
    void load(false);
    const onStorage = () => void load(true);
    const onSessionUpdated = () => void load(true);
    window.addEventListener("storage", onStorage);
    window.addEventListener(SESSION_UPDATED_EVENT, onSessionUpdated);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(SESSION_UPDATED_EVENT, onSessionUpdated);
    };
  }, [load]);

  return { context, me, loading, error, status, refresh };
}
