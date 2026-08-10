"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchJson } from "../lib/api";
import { resolveAgencySelection } from "../lib/agencyContext";
import { resolveApiBase } from "../lib/apiBase";
import { getSessionToken } from "../lib/sessionToken";
import type { AgencyClientAccessOut, AgencyMemberOut, AgencyOut } from "../lib/types";
import { useSessionContext } from "./useSessionContext";

export { agencySelectionRequiredMessage, resolveAgencySelection } from "../lib/agencyContext";

export const AGENCY_CONTEXT_UPDATED_EVENT = "ops-agency-context-updated";
const AGENCY_STORAGE_PREFIX = "ops_selected_agency_id";

type AgencyContextOptions = {
  apiBase?: string;
  token?: string;
  loadPortfolio?: boolean;
};

function agencyStorageKey(userId: string) {
  return `${AGENCY_STORAGE_PREFIX}:${userId}`;
}

export function useAgencyContext(options: AgencyContextOptions = {}) {
  const { context: sessionContext, loading: sessionLoading } = useSessionContext();
  const [agencies, setAgencies] = useState<AgencyOut[]>([]);
  const [selectedAgencyId, setSelectedAgencyIdState] = useState("");
  const [agenciesLoading, setAgenciesLoading] = useState(false);
  const [portfolioAgencyId, setPortfolioAgencyId] = useState("");
  const [bindings, setBindings] = useState<AgencyClientAccessOut[]>([]);
  const [currentMember, setCurrentMember] = useState<AgencyMemberOut | null>(null);
  const [portfolioLoading, setPortfolioLoading] = useState(false);
  const [portfolioError, setPortfolioError] = useState("");
  const [error, setError] = useState("");
  const agencyRequestEpoch = useRef(0);

  const role = sessionContext?.role || null;
  const userId = sessionContext?.user_id || "";
  const apiBase = options.apiBase || resolveApiBase(process.env.NEXT_PUBLIC_API_BASE || "/api/backend");
  const token = options.token === undefined ? getSessionToken() : options.token;
  const loadPortfolio = options.loadPortfolio === true;

  const applySelection = useCallback((nextAgencies: AgencyOut[]) => {
    if (!userId || typeof window === "undefined") {
      setSelectedAgencyIdState("");
      return;
    }
    const key = agencyStorageKey(userId);
    const nextId = resolveAgencySelection(nextAgencies, localStorage.getItem(key));
    setSelectedAgencyIdState(nextId);
    if (nextId) localStorage.setItem(key, nextId);
    else localStorage.removeItem(key);
  }, [userId]);

  const refresh = useCallback(async () => {
    const requestEpoch = ++agencyRequestEpoch.current;
    if (sessionLoading) return;
    if (role !== "agency" || !userId) {
      setAgencies([]);
      setSelectedAgencyIdState("");
      setError("");
      setAgenciesLoading(false);
      return;
    }

    setAgenciesLoading(true);
    try {
      const response = await fetchJson<{ items: AgencyOut[] }>(
        apiBase,
        "/platform/agencies?status=active",
        token,
      );
      if (requestEpoch !== agencyRequestEpoch.current) return;
      const rows = Array.isArray(response?.items)
        ? response.items.filter((agency) => agency.status === "active")
        : [];
      setAgencies(rows);
      applySelection(rows);
      setError(rows.length ? "" : "Для пользователя не найдено активное агентство.");
    } catch (reason) {
      if (requestEpoch !== agencyRequestEpoch.current) return;
      setAgencies([]);
      setSelectedAgencyIdState("");
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить список агентств.");
    } finally {
      if (requestEpoch === agencyRequestEpoch.current) setAgenciesLoading(false);
    }
  }, [apiBase, applySelection, role, sessionLoading, token, userId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!loadPortfolio || role !== "agency" || !selectedAgencyId || !userId) {
      setPortfolioAgencyId("");
      setBindings([]);
      setCurrentMember(null);
      setPortfolioLoading(false);
      setPortfolioError("");
      return;
    }

    let cancelled = false;
    const requestedAgencyId = selectedAgencyId;
    setPortfolioAgencyId("");
    setBindings([]);
    setCurrentMember(null);
    setPortfolioLoading(true);
    setPortfolioError("");
    void Promise.all([
      fetchJson<AgencyClientAccessOut[]>(
        apiBase,
        `/platform/agencies/${requestedAgencyId}/clients`,
        token,
      ),
      fetchJson<AgencyMemberOut[]>(
        apiBase,
        `/platform/agencies/${requestedAgencyId}/members`,
        token,
      ),
    ]).then(([bindingRows, memberRows]) => {
      if (cancelled) return;
      setBindings(Array.isArray(bindingRows) ? bindingRows : []);
      setCurrentMember(
        (Array.isArray(memberRows) ? memberRows : []).find(
          (member) => member.user_id === userId && member.status === "active",
        ) || null,
      );
      setPortfolioAgencyId(requestedAgencyId);
    }).catch((reason) => {
      if (cancelled) return;
      setBindings([]);
      setCurrentMember(null);
      setPortfolioAgencyId("");
      setPortfolioError(reason instanceof Error ? reason.message : "Не удалось загрузить портфель агентства.");
    }).finally(() => {
      if (!cancelled) setPortfolioLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [apiBase, loadPortfolio, role, selectedAgencyId, token, userId]);

  useEffect(() => {
    if (!userId || role !== "agency") return;
    const key = agencyStorageKey(userId);
    const syncSelection = (event?: Event) => {
      if (event instanceof StorageEvent && event.key && event.key !== key) return;
      const nextId = resolveAgencySelection(agencies, localStorage.getItem(key));
      setSelectedAgencyIdState(nextId);
    };
    window.addEventListener("storage", syncSelection);
    window.addEventListener(AGENCY_CONTEXT_UPDATED_EVENT, syncSelection);
    return () => {
      window.removeEventListener("storage", syncSelection);
      window.removeEventListener(AGENCY_CONTEXT_UPDATED_EVENT, syncSelection);
    };
  }, [agencies, role, userId]);

  const setSelectedAgencyId = useCallback((agencyId: string) => {
    if (!userId || role !== "agency") return;
    const nextId = agencies.some((agency) => agency.id === agencyId) ? agencyId : "";
    const key = agencyStorageKey(userId);
    if (nextId) localStorage.setItem(key, nextId);
    else localStorage.removeItem(key);
    setSelectedAgencyIdState(nextId);
    window.dispatchEvent(new CustomEvent(AGENCY_CONTEXT_UPDATED_EVENT, {
      detail: { agencyId: nextId, userId },
    }));
  }, [agencies, role, userId]);

  const selectedAgency = useMemo(
    () => agencies.find((agency) => agency.id === selectedAgencyId) || null,
    [agencies, selectedAgencyId],
  );
  const portfolioReady = role !== "agency"
    || !loadPortfolio
    || (!!selectedAgencyId && portfolioAgencyId === selectedAgencyId && !portfolioLoading);
  const clientIds = useMemo(
    () => (
      role === "agency" && loadPortfolio && portfolioAgencyId === selectedAgencyId
        ? bindings.map((binding) => binding.client_id)
        : role === "solo_client"
          ? Array.from(new Set(sessionContext?.accessible_client_ids || []))
        : []
    ),
    [bindings, loadPortfolio, portfolioAgencyId, role, selectedAgencyId, sessionContext?.accessible_client_ids],
  );
  const loading = sessionLoading
    || (role === "agency" && agenciesLoading)
    || (
      role === "agency"
      && loadPortfolio
      && !!selectedAgencyId
      && (portfolioLoading || (!portfolioReady && !portfolioError))
    );
  const selectionRequired = role === "agency" && agencies.length > 1 && !selectedAgencyId;
  const soloClientReady = role !== "solo_client" || clientIds.length === 1;
  const managedClientId = role === "solo_client" && soloClientReady ? clientIds[0] : "";

  return {
    role,
    sessionContext,
    agencies,
    selectedAgencyId: role === "agency" ? selectedAgencyId : "",
    selectedAgency,
    clientIds,
    bindings,
    currentMember,
    portfolioReady,
    portfolioError,
    selectionRequired,
    soloClientReady,
    managedClientId,
    loading,
    error,
    setSelectedAgencyId,
    refresh,
  };
}
