"use client";

import { useCallback } from "react";
import { fetchJson, getQuery } from "../lib/api";
import { OperationalAction } from "../lib/types";

export function useOperationalActions(apiBase: string, token: string) {
  const listActions = useCallback(
    async ({ clientId, accountId }: { clientId?: string; accountId?: string } = {}) => {
      const query = getQuery({ client_id: clientId, account_id: accountId });
      const rows = await fetchJson<OperationalAction[]>(apiBase, `/insights/operational/actions${query}`, token);
      return Array.isArray(rows) ? rows : [];
    },
    [apiBase, token]
  );

  const executeAction = useCallback(
    async (payload: {
      action: "scale" | "cap" | "pause" | "review";
      scope: "account" | "client" | "agency";
      scope_id: string;
      title: string;
      reason: string;
      metrics?: Record<string, unknown>;
      client_id?: string;
      account_id?: string;
    }) => {
      return fetchJson<OperationalAction>(apiBase, "/insights/operational/actions", token, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      });
    },
    [apiBase, token]
  );

  return { listActions, executeAction };
}
