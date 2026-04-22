"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ToastHost } from "../components/ToastHost";
import { AppSidebar } from "../components/AppSidebar";
import { AppTopTabs } from "../components/AppTopTabs";
import { ClientOperationsView } from "../components/views/ClientOperationsView";
import { DashboardView } from "../components/views/DashboardView";
import { useSession } from "../hooks/useSession";
import { useOperationalActions } from "../hooks/useOperationalActions";
import { useToast } from "../hooks/useToast";
import { fetchJson, getQuery } from "../lib/api";
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

function fmtMoney(v: number | null | undefined) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v || 0);
}

function fmtNum(v: number | null | undefined) {
  return new Intl.NumberFormat("en-US").format(v || 0);
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
  const out: string[] = [];
  const cur = new Date(from);
  while (cur <= to) {
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
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const router = useRouter();
  const { toasts, push } = useToast();
  const { executeAction, listActions } = useOperationalActions(session.apiBase, session.token);

  const [view, setView] = useState<"dashboard" | "client_ops">("dashboard");
  const [periodDays, setPeriodDays] = useState(30);
  const [clientId, setClientId] = useState("");
  const [platform, setPlatform] = useState<"all" | "meta" | "google" | "tiktok">("all");

  const [clients, setClients] = useState<Client[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [dailyRows, setDailyRows] = useState<AdStat[]>([]);
  const [operationalInsights, setOperationalInsights] = useState<OperationalInsight[]>([]);
  const [recentActions, setRecentActions] = useState<OperationalAction[]>([]);
  const [agencyOverview, setAgencyOverview] = useState<AgencyOverview | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);

  const [warning, setWarning] = useState("");
  const [authResolved, setAuthResolved] = useState(false);
  const [currentRole, setCurrentRole] = useState<"admin" | "agency" | "client" | "unknown">("unknown");

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
    const role = (me?.user?.role || "unknown") as "admin" | "agency" | "client" | "unknown";
    setCurrentRole(role);
    setAuthResolved(true);
    return role;
  }, [req]);

  const loadClients = useCallback(async () => {
    const payload = await req<{ items: Client[] }>("/clients?status=active");
    setClients(payload.items || []);
  }, [req]);

  const buildOverviewQuery = useCallback(() => {
    const r = dateRange(periodDays);
    return getQuery({ date_from: r.from, date_to: r.to, client_id: clientId || undefined });
  }, [periodDays, clientId]);

  const loadOverviewData = useCallback(async () => {
    const query = buildOverviewQuery();
    const [ov, stats, ops, acts] = await Promise.all([
      req<Overview>(`/insights/overview${query}`),
      req<{ items: AdStat[] }>(`/ad-stats${query}`),
      req<{ items: OperationalInsight[] }>(`/insights/operational${query}`),
      listActions({ clientId: clientId || undefined }),
    ]);
    setOverview(ov);
    setDailyRows(stats.items || []);
    setOperationalInsights(ops.items || []);
    setRecentActions(Array.isArray(acts) ? acts : []);
  }, [req, buildOverviewQuery, clientId, listActions]);

  const loadClientOpsData = useCallback(async () => {
    const r = dateRange(periodDays);
    const query = getQuery({ date_from: r.from, date_to: r.to });
    const [agency, bgs] = await Promise.all([
      req<AgencyOverview>(`/agency/overview${query}`),
      req<{ items: Budget[] }>(`/budgets${getQuery({ status: "active", date_from: r.from, date_to: r.to })}`),
    ]);
    setAgencyOverview(agency);
    setBudgets(bgs.items || []);
  }, [req, periodDays]);

  const refresh = useCallback(async () => {
    try {
      setWarning("");
      await Promise.all([loadOverviewData(), loadClientOpsData()]);
    } catch (err) {
      setWarning(err instanceof Error ? err.message : "Failed to load data");
    }
  }, [loadOverviewData, loadClientOpsData]);

  useEffect(() => {
    if (!ready) return;
    void resolveAuth()
      .then((role) => {
        if (role === "client") {
          router.replace("/portal");
          return;
        }
        void loadClients();
      })
      .catch((err) => {
        setWarning(err instanceof Error ? err.message : "Failed to resolve session");
        setAuthResolved(true);
      });
  }, [ready, resolveAuth, router, loadClients]);

useEffect(() => {
    if (clientId) return;
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
    if (!ready || !authResolved || currentRole === "client") return;
    void refresh();
  }, [ready, authResolved, currentRole, periodDays, clientId, platform, refresh]);

  const groupedTimeline = useMemo(() => {
    if (!overview) return [] as TimelinePoint[];
    const dates = buildPeriodDates(overview.range.date_from, overview.range.date_to);
    const map = new Map(dates.map((d) => [d, 0]));
    for (const r of dailyRows) {
      if (platform !== "all" && r.platform !== platform) continue;
      map.set(r.date, Number(map.get(r.date) || 0) + Number(r.spend || 0));
    }
    const points = [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    const budgetTotal = Number(overview.budget_summary.budget || 0);
    const expectedTotal = budgetTotal > 0 ? budgetTotal : Number(overview.budget_summary.expected_spend_to_date || 0);
    const totalPoints = points.length + TIMELINE_FUTURE_DAYS;
    const step = totalPoints ? expectedTotal / totalPoints : 0;
    let run = 0;
    const base = points.map(([k, v], i) => {
      run += Number(v || 0);
      return { date: k, label: k.slice(5), expected: step * (i + 1), actual: run };
    });
    const tail: TimelinePoint[] = [];
    const lastDate = parseIsoDate(overview.range.date_to);
    for (let i = 1; i <= TIMELINE_FUTURE_DAYS; i += 1) {
      const d = new Date(lastDate);
      d.setDate(d.getDate() + i);
      const iso = fmtLocalDate(d);
      tail.push({ date: iso, label: iso.slice(5), expected: step * (base.length + i), actual: null });
    }
    return [...base, ...tail];
  }, [overview, dailyRows, platform]);

  const timelineActions = useMemo(
    () =>
      (recentActions || [])
        .map((a) => ({ date: String(a.created_at || "").slice(0, 10), action: a.action, title: a.title }))
        .filter((x) => /^\d{4}-\d{2}-\d{2}$/.test(x.date))
        .slice(0, 16) as TimelineAction[],
    [recentActions]
  );

  const platformRows = useMemo(
    () => (overview ? (overview.breakdowns.platforms || []).filter((p) => platform === "all" || p.platform === platform) : []),
    [overview, platform]
  );

  const riskRows = useMemo(
    () =>
      overview
        ? [...(overview.breakdowns.accounts || [])]
            .filter((x) => platform === "all" || x.platform === platform)
            .sort((a, b) => Number(b.cpc || 0) - Number(a.cpc || 0))
            .slice(0, 8)
        : [],
    [overview, platform]
  );

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

    const lastActionByClient = new Map<string, OperationalAction>();
    for (const a of recentActions || []) {
      if (a.client_id && !lastActionByClient.has(a.client_id)) lastActionByClient.set(a.client_id, a);
    }

    return (clients || [])
      .map((c) => {
        const spend = Number(spendByClient.get(c.id)?.spend || 0);
        const budget = Number(clientBudgetMap.get(c.id)?.amount || 0);
        const usage = budget > 0 ? (spend / budget) * 100 : null;
        const pace: ClientOpsRow["pace"] = usage == null ? "no_budget" : usage >= 90 ? "critical" : usage >= 70 ? "warning" : "stable";
        const riskBase = usage == null ? 58 : usage;
        const riskScore = Math.max(1, Math.min(99, Math.round(riskBase + (spend / maxSpend) * 12)));
        const owner = (c.name || "NA")
          .split(" ")
          .map((x) => x[0] || "")
          .slice(0, 2)
          .join("")
          .toUpperCase();
        return {
          id: c.id,
          name: c.name,
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
  }, [agencyOverview, budgets, recentActions, clients]);

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
      setRecentActions(Array.isArray(acts) ? acts : []);
      push(`Action queued: ${row.action.toUpperCase()} for ${row.scope} ${row.scope_id}`, "success");
    },
    [executeAction, listActions, overview, clientId, push]
  );

  const runClientAlertAction = useCallback(
    async (row: ClientOpsRow, action: "cap" | "review") => {
      await executeAction({
        action,
        scope: "client",
        scope_id: row.id,
        title: `${action.toUpperCase()} for ${row.name}`,
        reason: "Triggered from Urgent Alerts panel",
        metrics: { risk_score: row.riskScore, usage: row.usage },
        client_id: row.id,
      });
      const acts = await listActions({ clientId: clientId || undefined });
      setRecentActions(Array.isArray(acts) ? acts : []);
      push(`Action queued: ${action.toUpperCase()} for ${row.name}`, "success");
    },
    [executeAction, listActions, clientId, push]
  );

  const asOfText = overview ? `As of ${overview.range.as_of_date} • ${overview.range.timezone_policy}` : "As of --";

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="dashboard" subtitle="Operations Center" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <div className="topbar-title">Editorial Rigor</div>
              <AppTopTabs active="dashboard" />
              <div className="chip-row" style={{ marginTop: 8 }}>
                <button className={`chip-btn ${view === "dashboard" ? "active" : ""}`} onClick={() => setView("dashboard")}>Overview Mode</button>
                <button className={`chip-btn ${view === "client_ops" ? "active" : ""}`} onClick={() => setView("client_ops")}>Client Ops Mode</button>
              </div>
            </div>
            {tokenLoginEnabled ? (
              <div className="session-controls">
                <input type="text" value={session.apiBase} onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))} placeholder="API base (http://localhost:8000)" />
                <input type="password" value={session.token} onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))} placeholder="Session token" />
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
                      setWarning(err instanceof Error ? err.message : "Save failed");
                    }
                  }}
                  disabled={!ready}
                >
                  Save
                </button>
              </div>
            ) : null}
          </header>

          <section className="filters">
            <label>
              Period
              <select value={String(periodDays)} onChange={(e) => setPeriodDays(Number(e.target.value))}>
                <option value="7">Last 7 Days</option>
                <option value="30">Last 30 Days</option>
                <option value="90">Last 90 Days</option>
              </select>
            </label>
            <label>
              Client
              <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
                <option value="">All Clients</option>
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Platform
              <select value={platform} onChange={(e) => setPlatform(e.target.value as "all" | "meta" | "google" | "tiktok")}>
                <option value="all">All Platforms</option>
                <option value="meta">Meta</option>
                <option value="google">Google</option>
                <option value="tiktok">TikTok</option>
              </select>
            </label>
            <div className="asof">{asOfText}</div>
            <button className="ghost-btn" onClick={() => void refresh()}>
              Apply Filters
            </button>
          </section>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          {view === "dashboard" ? (
            <DashboardView
              overview={overview}
              platform={platform}
              platformRows={platformRows}
              riskRows={riskRows}
              periodDays={periodDays}
              groupedTimeline={groupedTimeline}
              timelineActions={timelineActions}
              operationalInsights={operationalInsights}
              recentActions={recentActions}
              fmtMoney={fmtMoney}
              fmtNum={fmtNum}
              paceClass={paceClass}
              onInsightAction={runInsightAction}
              onRiskActionDraft={(accountId, label) => push(`Action draft: ${label} for account ${accountId}`, "info")}
            />
          ) : (
            <ClientOperationsView
              clientOpsRows={clientOpsRows}
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
