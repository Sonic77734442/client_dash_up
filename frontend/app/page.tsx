"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ToastHost } from "../components/ToastHost";
import { AppSidebar } from "../components/AppSidebar";
import { AppTopTabs } from "../components/AppTopTabs";
import { ClientOperationsView } from "../components/views/ClientOperationsView";
import { DashboardView } from "../components/views/DashboardView";
import { agencySelectionRequiredMessage, useAgencyContext } from "../hooks/useAgencyContext";
import { useSession } from "../hooks/useSession";
import { useScopeRequestGuard } from "../hooks/useScopeRequestGuard";
import { useOperationalActions } from "../hooks/useOperationalActions";
import { useToast } from "../hooks/useToast";
import { fetchJson, getQuery } from "../lib/api";
import {
  normalizeAgencyOverviewPayload,
  normalizeOverviewPayload,
} from "../lib/analyticsPayload";
import { isAppRole } from "../lib/authRedirect";
import { dataFreshnessMeta, metricRowsFreshness, overviewDataFreshness } from "../lib/dataFreshness";
import { hasStringFields, normalizeListPayload } from "../lib/listPayload";
import { calculateClientRiskScore } from "../lib/riskScore";
import {
  AdStat,
  AgencyOverview,
  AuthMeResponse,
  Budget,
  Client,
  ClientOpsRow,
  OperationalAction,
  OperationalInsight,
  Overview,
  TimelineAction,
  TimelinePoint,
} from "../lib/types";

const TIMELINE_FUTURE_DAYS = 2;

function isClientItem(value: unknown): value is Client {
  return hasStringFields(value, ["id", "name"]);
}

function isAdStatItem(value: unknown): value is AdStat {
  return hasStringFields(value, ["date", "platform"]);
}

function isInsightItem(value: unknown): value is OperationalInsight {
  return hasStringFields(value, ["scope", "scope_id", "title", "reason", "action", "priority"]);
}

function isActionItem(value: unknown): value is OperationalAction {
  return hasStringFields(value, [
    "id",
    "action",
    "scope",
    "scope_id",
    "status",
    "title",
    "created_at",
  ]);
}

function isBudgetItem(value: unknown): value is Budget {
  return hasStringFields(value, ["client_id", "scope", "amount", "updated_at"]);
}

function normalizeCurrency(value: string | null | undefined) {
  const currency = String(value || "USD").trim().toUpperCase();
  try {
    new Intl.NumberFormat("ru-RU", { style: "currency", currency }).format(0);
    return currency;
  } catch {
    return "USD";
  }
}

function fmtMoney(v: number | null | undefined, currency = "USD") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: normalizeCurrency(currency),
    maximumFractionDigits: 0,
  }).format(v || 0);
}

function fmtNum(v: number | null | undefined) {
  return new Intl.NumberFormat("ru-RU").format(v || 0);
}

function dateRange(periodDays: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (periodDays - 1));
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

function parseIsoDate(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function fmtLocalDate(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function buildPeriodDates(fromIso: string, toIso: string) {
  const from = parseIsoDate(fromIso);
  const to = parseIsoDate(toIso);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from > to) return [];
  const out: string[] = [];
  const cur = new Date(from);
  while (cur <= to && out.length < 370) {
    out.push(fmtLocalDate(cur));
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}

function paceClass(status: string) {
  if (status === "overspending") return "bad";
  if (status === "underspending") return "warn";
  return "good";
}

export default function HomePage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const agencyContext = useAgencyContext({ apiBase: session.apiBase, token: session.token, loadPortfolio: true });
  const agencyScopeKey = agencyContext.selectedAgencyId || agencyContext.role || "unknown";
  const beginClientsRequest = useScopeRequestGuard(agencyScopeKey);
  const beginOverviewRequest = useScopeRequestGuard(agencyScopeKey);
  const beginClientOpsRequest = useScopeRequestGuard(agencyScopeKey);
  const beginActionRequest = useScopeRequestGuard(agencyScopeKey);
  const router = useRouter();
  const { toasts, push } = useToast();
  const { executeAction, listActions } = useOperationalActions(session.apiBase, session.token);

  const [view, setView] = useState<"dashboard" | "client_ops">("dashboard");
  const initialRange = dateRange(30);
  const [periodDays, setPeriodDays] = useState(30);
  const [dateFrom, setDateFrom] = useState(initialRange.from);
  const [dateTo, setDateTo] = useState(initialRange.to);
  const [clientId, setClientId] = useState("");
  const [platform, setPlatform] = useState<"all" | "meta" | "google" | "tiktok">("all");

  const [clients, setClients] = useState<Client[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [dailyRows, setDailyRows] = useState<AdStat[]>([]);
  const [operationalInsights, setOperationalInsights] = useState<OperationalInsight[]>([]);
  const [recentActions, setRecentActions] = useState<OperationalAction[]>([]);
  const [agencyOverview, setAgencyOverview] = useState<AgencyOverview | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);

  const currencyByClient = useMemo(() => {
    const currencies = new Map<string, string>();
    for (const client of clients) {
      if (client.default_currency) currencies.set(client.id, normalizeCurrency(client.default_currency));
    }
    for (const budget of budgets) {
      if (!currencies.has(budget.client_id) && budget.currency) {
        currencies.set(budget.client_id, normalizeCurrency(budget.currency));
      }
    }
    for (const client of clients) {
      if (!currencies.has(client.id)) currencies.set(client.id, "USD");
    }
    return currencies;
  }, [clients, budgets]);

  const dashboardCurrency = useMemo(() => {
    if (clientId) return currencyByClient.get(clientId) || "USD";
    const unique = new Set(currencyByClient.values());
    return unique.size === 1 ? [...unique][0] : null;
  }, [clientId, currencyByClient]);

  const fmtDashboardMoney = useCallback(
    (value: number | null | undefined) =>
      dashboardCurrency ? fmtMoney(value, dashboardCurrency) : "Разные валюты",
    [dashboardCurrency]
  );

  const fmtScopedDashboardMoney = useCallback(
    (
      value: number | null | undefined,
      scope: "account" | "client" | "agency",
      scopeId: string,
    ) => {
      const scopedClientId =
        scope === "client"
          ? scopeId
          : scope === "account"
            ? overview?.breakdowns?.accounts?.find((row) => row.account_id === scopeId)?.client_id
            : null;
      const currency = scopedClientId ? currencyByClient.get(scopedClientId) : dashboardCurrency;
      return currency ? fmtMoney(value, currency) : "Разные валюты";
    },
    [currencyByClient, dashboardCurrency, overview]
  );

  const [warning, setWarning] = useState("");
  const [authResolved, setAuthResolved] = useState(false);
  const [currentRole, setCurrentRole] = useState<"admin" | "agency" | "client" | "solo_client" | "unknown">("unknown");
  const [adminMetricsMode, setAdminMetricsMode] = useState(false);

  const [clientOpsSearch, setClientOpsSearch] = useState("");
  const [clientOpsChip, setClientOpsChip] = useState<"all" | "at_risk" | "overspending" | "no_budget" | "has_alerts">("all");
  const [density, setDensity] = useState<"comfortable" | "compact">("comfortable");
  const [sortBy, setSortBy] = useState<"name" | "spend" | "budget" | "usage" | "pace" | "riskScore">("riskScore");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);


  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const resolveAuth = useCallback(async () => {
    const me = await req<AuthMeResponse>("/auth/me");
    const role = me?.user?.role;
    if (!isAppRole(role)) {
      throw new Error("Сессия содержит неизвестную роль. Войдите заново.");
    }
    setCurrentRole(role);
    setAuthResolved(true);
    return role;
  }, [req]);

  const loadClients = useCallback(async () => {
    const isCurrentRequest = beginClientsRequest();
    if (
      agencyContext.role === "agency"
      && (!agencyContext.selectedAgencyId || !agencyContext.portfolioReady)
    ) {
      setClients([]);
      throw new Error(
        agencyContext.selectionRequired
          ? agencySelectionRequiredMessage()
          : agencyContext.portfolioError || agencyContext.error || "Не удалось загрузить портфель агентства.",
      );
    }
    const payload = await req<unknown>("/clients?status=active");
    if (!isCurrentRequest()) return;
    const rows = normalizeListPayload(payload, isClientItem, "клиентов");
    const allowedIds = agencyContext.role === "agency" ? new Set(agencyContext.clientIds) : null;
    setClients(allowedIds ? rows.filter((client) => allowedIds.has(client.id)) : rows);
  }, [
    agencyContext.clientIds,
    agencyContext.error,
    agencyContext.portfolioError,
    agencyContext.portfolioReady,
    agencyContext.role,
    agencyContext.selectedAgencyId,
    agencyContext.selectionRequired,
    beginClientsRequest,
    req,
  ]);

  const buildOverviewQuery = useCallback(() => {
    return getQuery({ date_from: dateFrom, date_to: dateTo, client_id: clientId || undefined });
  }, [dateFrom, dateTo, clientId]);

  const loadOverviewData = useCallback(async () => {
    const isCurrentRequest = beginOverviewRequest();
    if (
      agencyContext.role === "agency"
      && (
        !agencyContext.portfolioReady
        || !clientId
        || !agencyContext.clientIds.includes(clientId)
      )
    ) {
      return;
    }
    const query = buildOverviewQuery();
    const statsQuery = getQuery({
      date_from: dateFrom,
      date_to: dateTo,
      client_id: clientId || undefined,
      platform: platform === "all" ? undefined : platform,
    });
    const [ov, stats, ops, acts] = await Promise.all([
      req<unknown>(`/insights/overview${query}`),
      req<unknown>(`/ad-stats${statsQuery}`),
      req<unknown>(`/insights/operational${query}`),
      listActions({ clientId: clientId || undefined }),
    ]);
    const nextOverview = normalizeOverviewPayload(ov);
    const nextStats = normalizeListPayload(stats, isAdStatItem, "статистики");
    const nextInsights = normalizeListPayload(ops, isInsightItem, "рекомендаций");
    const nextActions = normalizeListPayload(acts, isActionItem, "действий");

    if (!isCurrentRequest()) return;

    setOverview(nextOverview);
    setDailyRows(nextStats);
    setOperationalInsights(nextInsights);
    setRecentActions(nextActions);
  }, [
    agencyContext.clientIds,
    agencyContext.portfolioReady,
    agencyContext.role,
    req,
    buildOverviewQuery,
    clientId,
    listActions,
    dateFrom,
    dateTo,
    platform,
    beginOverviewRequest,
  ]);

  const loadClientOpsData = useCallback(async () => {
    const isCurrentRequest = beginClientOpsRequest();
    if (agencyContext.role === "agency" && !agencyContext.portfolioReady) return;
    const query = getQuery({ date_from: dateFrom, date_to: dateTo });
    const [agency, bgs] = await Promise.all([
      req<unknown>(`/agency/overview${query}`),
      req<unknown>(`/budgets${getQuery({ status: "active", date_from: dateFrom, date_to: dateTo })}`),
    ]);
    const nextAgencyOverview = normalizeAgencyOverviewPayload(agency);
    const nextBudgets = normalizeListPayload(bgs, isBudgetItem, "бюджетов");
    if (!isCurrentRequest()) return;
    const allowedIds = agencyContext.role === "agency" ? new Set(agencyContext.clientIds) : null;
    const visibleAgencyOverview = allowedIds
      ? {
          ...nextAgencyOverview,
          totals: {
            ...nextAgencyOverview.totals,
            spend: (nextAgencyOverview.per_client || [])
              .filter((row) => allowedIds.has(row.client_id))
              .reduce((sum, row) => sum + Number(row.spend || 0), 0),
          },
          per_client: (nextAgencyOverview.per_client || []).filter((row) => allowedIds.has(row.client_id)),
          per_account: (nextAgencyOverview.per_account || []).filter((row) => allowedIds.has(row.client_id)),
        }
      : nextAgencyOverview;
    setAgencyOverview(visibleAgencyOverview);
    setBudgets(allowedIds ? nextBudgets.filter((budget) => allowedIds.has(budget.client_id)) : nextBudgets);
  }, [agencyContext.clientIds, agencyContext.portfolioReady, agencyContext.role, beginClientOpsRequest, req, dateFrom, dateTo]);

  const refresh = useCallback(async () => {
    try {
      setWarning("");
      await Promise.all([loadOverviewData(), loadClientOpsData()]);
    } catch (err) {
      setWarning(err instanceof Error ? err.message : "Не удалось загрузить данные");
    }
  }, [loadOverviewData, loadClientOpsData]);

  useEffect(() => {
    setAdminMetricsMode(new URLSearchParams(window.location.search).get("admin_metrics") === "1");
  }, []);

  useEffect(() => {
    if (!ready || agencyContext.loading) return;
    void resolveAuth()
      .then((role) => {
        if (role === "client" || role === "solo_client") {
          router.replace("/portal");
          return;
        }
        if (role === "admin" && new URLSearchParams(window.location.search).get("admin_metrics") !== "1") {
          router.replace("/platform");
          return;
        }
        void loadClients();
      })
      .catch((err) => {
        setWarning(err instanceof Error ? err.message : "Не удалось проверить сессию");
        setAuthResolved(true);
      });
  }, [agencyContext.loading, ready, resolveAuth, router, loadClients]);

  useEffect(() => {
    setClientId("");
    setClients([]);
    setOverview(null);
    setDailyRows([]);
    setOperationalInsights([]);
    setRecentActions([]);
    setAgencyOverview(null);
    setBudgets([]);
  }, [agencyContext.selectedAgencyId]);

  useEffect(() => {
    if (clientId && clients.some((client) => client.id === clientId)) return;
    if (currentRole === "agency" && clients.length > 0) {
      setClientId(clients[0].id);
      return;
    }
    if (clients.length === 1) {
      setClientId(clients[0].id);
    }
  }, [clients, clientId, currentRole]);

  useEffect(() => {
    if (currentRole === "agency" && !clientId) return;
    if (currentRole === "agency" && !agencyContext.portfolioReady) return;
    if (!ready || !authResolved || (currentRole !== "admin" && currentRole !== "agency")) return;
    void refresh();
  }, [agencyContext.portfolioReady, ready, authResolved, currentRole, dateFrom, dateTo, clientId, platform, refresh]);

  useEffect(() => {
    const from = new Date(`${dateFrom}T00:00:00`);
    const to = new Date(`${dateTo}T00:00:00`);
    if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from > to) return;
    const days = Math.floor((to.getTime() - from.getTime()) / 86400000) + 1;
    if (days > 0) setPeriodDays(days);
  }, [dateFrom, dateTo]);

  const applyQuickRange = useCallback((days: number) => {
    const r = dateRange(days);
    setDateFrom(r.from);
    setDateTo(r.to);
    setPeriodDays(days);
  }, []);

  const effectiveOverview = useMemo<Overview | null>(() => {
    if (!overview || platform === "all") return overview;

    const rows = dailyRows.filter((row) => row.platform === platform);
    const spend = rows.reduce((sum, row) => sum + Number(row.spend || 0), 0);
    const impressions = rows.reduce((sum, row) => sum + Number(row.impressions || 0), 0);
    const clicks = rows.reduce((sum, row) => sum + Number(row.clicks || 0), 0);
    const conversions = rows.reduce((sum, row) => sum + Number(row.conversions || 0), 0);

    return {
      ...overview,
      spend_summary: {
        spend,
        impressions,
        clicks,
        conversions,
        ctr: impressions > 0 ? clicks / impressions : 0,
        cpc: clicks > 0 ? spend / clicks : 0,
        cpm: impressions > 0 ? (spend * 1000) / impressions : 0,
      },
      budget_summary: {
        ...(overview.budget_summary || {}),
        budget: null,
        spend,
        remaining: null,
        usage_percent: null,
        expected_spend_to_date: null,
        forecast_spend: null,
        pace_status: "not_applicable",
        pace_delta: null,
        pace_delta_percent: null,
      },
      breakdowns: {
        platforms: (overview.breakdowns?.platforms || []).filter((row) => row.platform === platform),
        accounts: (overview.breakdowns?.accounts || []).filter((row) => row.platform === platform),
      },
    };
  }, [overview, dailyRows, platform]);

  const platformAccountIds = useMemo(
    () => new Set((effectiveOverview?.breakdowns?.accounts || []).map((row) => row.account_id)),
    [effectiveOverview]
  );

  const visibleOperationalInsights = useMemo(
    () =>
      platform === "all"
        ? operationalInsights
        : operationalInsights.filter(
            (row) => row.scope === "account" && platformAccountIds.has(row.scope_id)
          ),
    [operationalInsights, platform, platformAccountIds]
  );

  const visibleRecentActions = useMemo(
    () =>
      platform === "all"
        ? recentActions
        : recentActions.filter((row) => {
            const accountId = row.account_id || (row.scope === "account" ? row.scope_id : null);
            return Boolean(accountId && platformAccountIds.has(accountId));
          }),
    [recentActions, platform, platformAccountIds]
  );

  const groupedTimeline = useMemo(() => {
    const overviewRange = effectiveOverview?.range;
    if (!overviewRange?.date_from || !overviewRange.date_to) return [] as TimelinePoint[];
    const dates = buildPeriodDates(overviewRange.date_from, overviewRange.date_to);
    const map = new Map(dates.map((d) => [d, 0]));
    for (const r of dailyRows) {
      if (platform !== "all" && r.platform !== platform) continue;
      map.set(r.date, Number(map.get(r.date) || 0) + Number(r.spend || 0));
    }
    const points = [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    const budgetTotal = Number(effectiveOverview?.budget_summary?.budget || 0);
    const expectedTotal =
      budgetTotal > 0
        ? budgetTotal
        : Number(effectiveOverview?.budget_summary?.expected_spend_to_date || 0);
    const totalPoints = points.length + TIMELINE_FUTURE_DAYS;
    const step = totalPoints ? expectedTotal / totalPoints : 0;
    let run = 0;
    const base = points.map(([k, v], i) => {
      run += Number(v || 0);
      return { date: k, label: k.slice(5), expected: step * (i + 1), actual: run };
    });
    const tail: TimelinePoint[] = [];
    const lastDate = parseIsoDate(overviewRange.date_to);
    for (let i = 1; i <= TIMELINE_FUTURE_DAYS; i += 1) {
      const d = new Date(lastDate);
      d.setDate(d.getDate() + i);
      const iso = fmtLocalDate(d);
      tail.push({ date: iso, label: iso.slice(5), expected: step * (base.length + i), actual: null });
    }
    return [...base, ...tail];
  }, [effectiveOverview, dailyRows, platform]);

  const timelineActions = useMemo(
    () =>
      (visibleRecentActions || [])
        .map((a) => ({ date: String(a.created_at || "").slice(0, 10), action: a.action, title: a.title }))
        .filter((x) => /^\d{4}-\d{2}-\d{2}$/.test(x.date))
        .slice(0, 16) as TimelineAction[],
    [visibleRecentActions]
  );

  const platformRows = useMemo(
    () => effectiveOverview?.breakdowns?.platforms || [],
    [effectiveOverview]
  );

  const riskRows = useMemo(
    () =>
      effectiveOverview
        ? [...(effectiveOverview.breakdowns?.accounts || [])]
            .sort((a, b) => Number(b.cpc || 0) - Number(a.cpc || 0))
            .slice(0, 8)
        : [],
    [effectiveOverview]
  );

  const dashboardDataState = useMemo(
    () => platform === "all"
      ? overviewDataFreshness(effectiveOverview)
      : metricRowsFreshness(dailyRows.filter((row) => row.platform === platform)),
    [dailyRows, effectiveOverview, platform]
  );
  const dashboardDataMeta = dataFreshnessMeta(dashboardDataState);

  const clientOpsRows = useMemo(() => {
    if (!agencyOverview) return [] as ClientOpsRow[];
    const clientBudgetMap = new Map<string, Budget>();
    for (const b of budgets) {
      if (b.scope !== "client") continue;
      const prev = clientBudgetMap.get(b.client_id);
      if (!prev || new Date(b.updated_at) > new Date(prev.updated_at)) clientBudgetMap.set(b.client_id, b);
    }

    const spendByClient = new Map<string, { spend: number }>();
    for (const row of agencyOverview.per_client || []) spendByClient.set(row.client_id, row);
    const maxSpend = Math.max(1, ...(agencyOverview.per_client || []).map((x) => Number(x.spend || 0)));
    const hasMixedClientCurrencies = new Set(
      clients.map((client) => currencyByClient.get(client.id) || "USD"),
    ).size > 1;

    const lastActionByClient = new Map<string, OperationalAction>();
    for (const a of recentActions || []) {
      if (a.client_id && !lastActionByClient.has(a.client_id)) lastActionByClient.set(a.client_id, a);
    }

    return (clients || [])
      .map((c) => {
        const spend = Number(spendByClient.get(c.id)?.spend || 0);
        const budget = Number(clientBudgetMap.get(c.id)?.amount || 0);
        const currency = currencyByClient.get(c.id) || "USD";
        const usage = budget > 0 ? (spend / budget) * 100 : null;
        const pace: ClientOpsRow["pace"] = usage == null ? "no_budget" : usage >= 90 ? "critical" : usage >= 70 ? "warning" : "stable";
        const riskScore = calculateClientRiskScore(usage, spend, maxSpend, !hasMixedClientCurrencies);
        const owner = (c.name || "NA")
          .split(" ")
          .map((x) => x[0] || "")
          .slice(0, 2)
          .join("")
          .toUpperCase();
        return {
          id: c.id,
          name: c.name,
          currency,
          spend,
          budget,
          usage,
          pace,
          riskScore,
          hasAlerts: pace === "critical" || pace === "warning",
          owner,
          lastAction: lastActionByClient.get(c.id) || null,
        };
      })
      .sort((a, b) => b.riskScore - a.riskScore);
  }, [agencyOverview, budgets, recentActions, clients, currencyByClient]);

  const filteredClientOpsRows = useMemo(() => {
    let rows = [...clientOpsRows];
    const q = clientOpsSearch.trim().toLowerCase();
    if (q) rows = rows.filter((r) => `${r.name} ${r.id} ${r.owner}`.toLowerCase().includes(q));
    if (clientOpsChip === "at_risk") rows = rows.filter((r) => r.riskScore >= 70);
    if (clientOpsChip === "overspending") rows = rows.filter((r) => (r.usage || 0) >= 100);
    if (clientOpsChip === "no_budget") rows = rows.filter((r) => !r.budget);
    if (clientOpsChip === "has_alerts") rows = rows.filter((r) => r.hasAlerts);

    const mul = sortDir === "asc" ? 1 : -1;
    const paceRank: Record<string, number> = { critical: 3, warning: 2, stable: 1, no_budget: 0 };
    rows.sort((a, b) => {
      const av =
        sortBy === "pace"
          ? paceRank[a.pace] || 0
          : sortBy === "name"
          ? String(a.name || "").toLowerCase()
          : Number((a as unknown as Record<string, unknown>)[sortBy] ?? 0);
      const bv =
        sortBy === "pace"
          ? paceRank[b.pace] || 0
          : sortBy === "name"
          ? String(b.name || "").toLowerCase()
          : Number((b as unknown as Record<string, unknown>)[sortBy] ?? 0);
      if (av < bv) return -1 * mul;
      if (av > bv) return 1 * mul;
      return 0;
    });

    return rows;
  }, [clientOpsRows, clientOpsSearch, clientOpsChip, sortBy, sortDir]);

  const pageSize = 10;
  const pages = Math.max(1, Math.ceil(filteredClientOpsRows.length / pageSize));
  const pagedClientOpsRows = useMemo(() => {
    const safePage = Math.max(1, Math.min(page, pages));
    const start = (safePage - 1) * pageSize;
    return filteredClientOpsRows.slice(start, start + pageSize);
  }, [filteredClientOpsRows, page, pages]);

  useEffect(() => {
    setPage((p) => Math.max(1, Math.min(p, pages)));
  }, [pages]);

  const runInsightAction = useCallback(
    async (row: OperationalInsight) => {
      const scopedClientId = row.scope === "client"
        ? row.scope_id
        : overview?.scope?.client_id || clientId;
      if (
        currentRole === "agency"
        && (!agencyContext.portfolioReady || !scopedClientId || !agencyContext.clientIds.includes(scopedClientId))
      ) {
        push("Действие недоступно: клиент не входит в выбранное агентство.", "error");
        return;
      }
      const isCurrentRequest = beginActionRequest();
      try {
        const payload: Record<string, unknown> = {
          action: row.action,
          scope: row.scope,
          scope_id: row.scope_id,
          title: row.title,
          reason: row.reason,
          metrics: row.metrics || {},
        };
        if (row.scope === "account") payload.account_id = row.scope_id;
        if (row.scope === "client") payload.client_id = row.scope_id;
        if (overview?.scope?.client_id && !payload.client_id) payload.client_id = overview.scope.client_id;

        await executeAction({
          action: row.action,
          scope: row.scope,
          scope_id: row.scope_id,
          title: row.title,
          reason: row.reason,
          metrics: (row.metrics || {}) as Record<string, unknown>,
          client_id: payload.client_id as string | undefined,
          account_id: payload.account_id as string | undefined,
        });
        const acts = await listActions({ clientId: clientId || undefined });
        if (isCurrentRequest()) setRecentActions(Array.isArray(acts) ? acts : []);
        push("Задача добавлена в очередь. Рекламный кабинет автоматически не изменён.", "success");
      } catch (error) {
        push(error instanceof Error ? error.message : "Не удалось создать задачу", "error");
      }
    },
    [
      agencyContext.clientIds,
      agencyContext.portfolioReady,
      beginActionRequest,
      clientId,
      currentRole,
      executeAction,
      listActions,
      overview,
      push,
    ]
  );

  const runClientAlertAction = useCallback(
    async (row: ClientOpsRow, action: "cap" | "review") => {
      if (
        currentRole === "agency"
        && (!agencyContext.portfolioReady || !agencyContext.clientIds.includes(row.id))
      ) {
        push("Действие недоступно: клиент не входит в выбранное агентство.", "error");
        return;
      }
      const isCurrentRequest = beginActionRequest();
      try {
        await executeAction({
          action,
          scope: "client",
          scope_id: row.id,
          title: `${action.toUpperCase()} for ${row.name}`,
          reason: "Создано из блока клиентов, требующих внимания.",
          metrics: { risk_score: row.riskScore, usage: row.usage },
          client_id: row.id,
        });
        const acts = await listActions({ clientId: clientId || undefined });
        if (isCurrentRequest()) setRecentActions(Array.isArray(acts) ? acts : []);
        push(`Задача для ${row.name} добавлена в очередь`, "success");
      } catch (error) {
        push(error instanceof Error ? error.message : "Не удалось создать задачу", "error");
      }
    },
    [agencyContext.clientIds, agencyContext.portfolioReady, beginActionRequest, clientId, currentRole, executeAction, listActions, push]
  );

  const runAccountAction = useCallback(
    async (accountId: string, label: string) => {
      const action: "cap" | "scale" | "review" =
        label.toLocaleLowerCase("ru").includes("масштаб")
          ? "scale"
          : label.toLocaleLowerCase("ru").includes("огранич")
          ? "cap"
          : "review";
      if (
        currentRole === "agency"
        && (
          !agencyContext.portfolioReady
          || !clientId
          || !agencyContext.clientIds.includes(clientId)
          || !overview?.breakdowns?.accounts?.some((account) => account.account_id === accountId && account.client_id === clientId)
        )
      ) {
        push("Действие недоступно: аккаунт не входит в выбранное агентство.", "error");
        return;
      }
      const isCurrentRequest = beginActionRequest();
      try {
        await executeAction({
          action,
          scope: "account",
          scope_id: accountId,
          title: label,
          reason: "Задача создана из центра эффективности после проверки показателей аккаунта.",
          metrics: {},
          client_id: clientId || undefined,
          account_id: accountId,
        });
        const acts = await listActions({ clientId: clientId || undefined });
        if (isCurrentRequest()) setRecentActions(Array.isArray(acts) ? acts : []);
        push("Задача добавлена в очередь. Изменение ещё не применено к рекламной платформе.", "success");
      } catch (error) {
        push(error instanceof Error ? error.message : "Не удалось создать задачу", "error");
      }
    },
    [
      agencyContext.clientIds,
      agencyContext.portfolioReady,
      beginActionRequest,
      clientId,
      currentRole,
      executeAction,
      listActions,
      overview,
      push,
    ]
  );

  const asOfText = overview ? `Данные на ${overview.range.as_of_date} • ${overview.range.timezone_policy}` : "Данные обновляются";

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="dashboard" subtitle="Рабочее пространство агентства" />

        <main className="content">
          {currentRole === "admin" && adminMetricsMode ? (
            <div className="admin-observer-banner">
              <div>
                <strong>Режим наблюдателя администратора</strong>
                <span> Вы просматриваете рекламные показатели с глобальным доступом.</span>
              </div>
              <Link className="ghost-btn" href="/platform">Вернуться в админку</Link>
            </div>
          ) : null}
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs active="dashboard" />
              <div className="topbar-title">Центр эффективности</div>
              <div className="panel-subtitle">Показатели, отклонения и решения по всем клиентам агентства</div>
              <div className="chip-row" style={{ marginTop: 6 }}>
                <button className={`chip-btn ${view === "dashboard" ? "active" : ""}`} onClick={() => setView("dashboard")}>Обзор показателей</button>
                <button className={`chip-btn ${view === "client_ops" ? "active" : ""}`} onClick={() => setView("client_ops")}>Портфель клиентов</button>
              </div>
            </div>
            {tokenLoginEnabled ? (
              <details className="debug-session">
                <summary>Подключение</summary>
                <div className="debug-session-popover">
                  <input type="text" value={session.apiBase} onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))} placeholder="API адрес" />
                  <input type="password" value={session.token} onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))} placeholder="Токен сессии" />
                  <button
                    className="ghost-btn"
                    onClick={async () => {
                      const apiBase = session.apiBase.trim().replace(/\/$/, "") || defaultApiBase;
                      const token = session.token.trim();
                      const next = { apiBase, token };
                      persist(next);
                      setSession(next);
                      try {
                        await loadClients();
                        await refresh();
                      } catch (err) {
                        setWarning(err instanceof Error ? err.message : "Не удалось сохранить подключение");
                      }
                    }}
                    disabled={!ready}
                  >
                    Сохранить
                  </button>
                </div>
              </details>
            ) : null}
          </header>

          <section className="filters">
            <label>
              Период
              <div className="chip-row" style={{ marginTop: 6 }}>
                <button className={`chip-btn ${periodDays === 7 ? "active" : ""}`} onClick={() => applyQuickRange(7)}>7 дней</button>
                <button className={`chip-btn ${periodDays === 15 ? "active" : ""}`} onClick={() => applyQuickRange(15)}>15 дней</button>
                <button className={`chip-btn ${periodDays === 30 ? "active" : ""}`} onClick={() => applyQuickRange(30)}>30 дней</button>
              </div>
            </label>
            <label>
              Дата с
              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            </label>
            <label>
              Дата по
              <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            </label>
            <label>
              Клиент
              <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
                <option value="">Все клиенты</option>
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Платформа
              <select value={platform} onChange={(e) => setPlatform(e.target.value as "all" | "meta" | "google" | "tiktok")}>
                <option value="all">Все платформы</option>
                <option value="meta">Meta</option>
                <option value="google">Google</option>
                <option value="tiktok">TikTok</option>
              </select>
            </label>
            <div className="asof">{asOfText}</div>
            <button className="ghost-btn" onClick={() => void refresh()}>
              Применить
            </button>
          </section>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>
          {!dashboardCurrency && view === "dashboard" ? (
            <div className="warning">
              В выборке несколько валют. Выберите одного клиента, чтобы денежные KPI и темп бюджета были сопоставимы.
            </div>
          ) : null}

          {view === "dashboard" ? (
            <DashboardView
              overview={effectiveOverview}
              dataState={dashboardDataState}
              dataNotice={dashboardDataMeta.description}
              platform={platform}
              platformRows={platformRows}
              riskRows={riskRows}
              periodDays={periodDays}
              groupedTimeline={groupedTimeline}
              timelineActions={timelineActions}
              operationalInsights={visibleOperationalInsights}
              recentActions={visibleRecentActions}
              fmtMoney={fmtDashboardMoney}
              fmtScopedMoney={fmtScopedDashboardMoney}
              fmtNum={fmtNum}
              paceClass={paceClass}
              onInsightAction={runInsightAction}
              onRiskActionDraft={runAccountAction}
            />
          ) : (
            <ClientOperationsView
              filteredClientOpsRows={filteredClientOpsRows}
              pagedClientOpsRows={pagedClientOpsRows}
              clients={clients}
              recentActions={recentActions}
              clientOpsSearch={clientOpsSearch}
              setClientOpsSearch={setClientOpsSearch}
              clientOpsChip={clientOpsChip}
              setClientOpsChip={setClientOpsChip}
              density={density}
              setDensity={setDensity}
              sortBy={sortBy}
              sortDir={sortDir}
              setSortBy={setSortBy}
              setSortDir={setSortDir}
              page={page}
              pages={pages}
              pageSize={pageSize}
              setPage={setPage}
              onOpenClient={(id) => router.push(`/client/${id}`)}
              onAlertAction={runClientAlertAction}
              fmtMoney={fmtMoney}
            />
          )}
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
