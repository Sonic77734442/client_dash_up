"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ToastHost } from "./ToastHost";
import { useSession } from "../hooks/useSession";
import { useToast } from "../hooks/useToast";
import { fetchJson, getQuery } from "../lib/api";
import {
  AdAccount,
  AdStat,
  AuthMeResponse,
  Budget,
  OperationalAction,
  OperationalInsight,
  Overview,
  SessionContext,
} from "../lib/types";

type ClientPortalTab = "overview" | "reports" | "billing";

function fmtMoney(v: number | null | undefined) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v || 0);
}

function fmtNum(v: number | null | undefined) {
  return new Intl.NumberFormat("en-US").format(v || 0);
}

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function dateRange(periodDays: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (periodDays - 1));
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

function itemClass(tab: ClientPortalTab, activeTab: ClientPortalTab) {
  return `menu-item ${tab === activeTab ? "active" : ""}`.trim();
}

const TAB_META: Record<ClientPortalTab, { title: string; subtitle: string }> = {
  overview: { title: "Client Overview", subtitle: "Read-only performance and budget tracking" },
  reports: { title: "Client Reports", subtitle: "Performance reports scoped by agency access" },
  billing: { title: "Client Billing", subtitle: "Budget and billing visibility scoped by agency access" },
};

export function ClientPortalPage({ activeTab }: { activeTab: ClientPortalTab }) {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [periodDays, setPeriodDays] = useState(30);
  const [warning, setWarning] = useState("");
  const [ctx, setCtx] = useState<SessionContext | null>(null);
  const [selectedClientId, setSelectedClientId] = useState("");

  const [overview, setOverview] = useState<Overview | null>(null);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [actions, setActions] = useState<OperationalAction[]>([]);
  const [stats, setStats] = useState<AdStat[]>([]);
  const [insights, setInsights] = useState<OperationalInsight[]>([]);

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadContext = useCallback(async () => {
    const payload = await fetchJson<AuthMeResponse>(session.apiBase, "/auth/me", session.token);
    setCtx(payload.session);
    return payload.session;
  }, [session.apiBase, session.token]);

  const loadData = useCallback(async () => {
    const context = await loadContext();
    if (!context?.valid) {
      setWarning("Invalid or expired session. Please sign in again.");
      return;
    }
    if (context.role !== "client") {
      setWarning("This portal is client-only. Use agency console for admin/agency roles.");
      return;
    }

    const available = context.accessible_client_ids || [];
    const cid = selectedClientId && available.includes(selectedClientId) ? selectedClientId : available[0] || "";
    setSelectedClientId(cid);
    if (!cid) {
      setWarning("No assigned client scope for this session.");
      return;
    }

    const r = dateRange(periodDays);
    const q = getQuery({ client_id: cid, date_from: r.from, date_to: r.to });
    const [ov, acc, bgs, acts, daily, ops] = await Promise.all([
      req<Overview>(`/insights/overview${q}`),
      req<{ items: AdAccount[] }>(`/ad-accounts${getQuery({ client_id: cid, status: "active" })}`),
      req<{ items: Budget[] }>(`/budgets${getQuery({ client_id: cid, status: "active", date_from: r.from, date_to: r.to })}`),
      req<OperationalAction[]>(`/insights/operational/actions${getQuery({ client_id: cid })}`),
      req<{ items: AdStat[] }>(`/ad-stats${q}`),
      req<{ items: OperationalInsight[] }>(`/insights/operational${q}`),
    ]);

    setOverview(ov);
    setAccounts(acc.items || []);
    setBudgets(bgs.items || []);
    setActions(Array.isArray(acts) ? acts.slice(0, 10) : []);
    setStats(daily.items || []);
    setInsights(ops.items || []);
    setWarning("");
  }, [loadContext, periodDays, req, selectedClientId]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) => {
      const msg = err instanceof Error ? err.message : "Failed to load portal";
      if (/unauthorized|401/i.test(msg)) {
        setWarning("Session expired. Redirecting to sign in...");
        window.location.replace("/login");
        return;
      }
      setWarning(msg);
    });
  }, [ready, loadData]);

  const kpis = useMemo(() => {
    const spend = Number(overview?.spend_summary?.spend || 0);
    const budget = Number(overview?.budget_summary?.budget || 0);
    const usage = overview?.budget_summary?.usage_percent;
    const pace = String(overview?.budget_summary?.pace_status || "on_track");
    return { spend, budget, usage, pace };
  }, [overview]);

  const spendByDate = useMemo(() => {
    const map = new Map<string, number>();
    for (const row of stats || []) {
      map.set(row.date, Number(map.get(row.date) || 0) + Number(row.spend || 0));
    }
    return [...map.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([date, spend]) => ({ date, spend }));
  }, [stats]);

  const tabMeta = TAB_META[activeTab];

  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">Editorial Rigor</div>
          <div className="panel-subtitle">Client Portal</div>
          <nav className="menu">
            <Link className={itemClass("overview", activeTab)} href="/portal">Overview</Link>
            <Link className={itemClass("reports", activeTab)} href="/portal/reports">Reports</Link>
            <Link className={itemClass("billing", activeTab)} href="/portal/billing">Billing</Link>
          </nav>
          <div className="sidebar-footer">
            <Link className="menu-item" href="/portal/reports">Export Reports</Link>
            <Link className="menu-item" href="/portal/billing">Budgets & Billing</Link>
            <Link className="menu-item" href="/">Open Agency Console</Link>
          </div>
        </aside>

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <div className="topbar-title">{tabMeta.title}</div>
              <div className="panel-subtitle">{tabMeta.subtitle}</div>
            </div>
            {tokenLoginEnabled ? (
              <div className="session-controls">
                <input
                  type="text"
                  value={session.apiBase}
                  onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))}
                  placeholder="API base"
                />
                <input
                  type="password"
                  value={session.token}
                  onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))}
                  placeholder="Session token"
                />
                <button
                  className="ghost-btn"
                  onClick={async () => {
                    const next = { apiBase: session.apiBase.trim().replace(/\/$/, "") || defaultApiBase, token: session.token.trim() };
                    persist(next);
                    setSession(next);
                    await loadData();
                    push("Session saved", "success");
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
              Scope
              <select value={selectedClientId} onChange={(e) => setSelectedClientId(e.target.value)}>
                {(ctx?.accessible_client_ids || []).map((id) => (
                  <option key={id} value={id}>{id.slice(0, 8)}</option>
                ))}
              </select>
            </label>
            <button className="ghost-btn" onClick={() => void loadData()}>Refresh</button>
          </section>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          {activeTab === "overview" ? (
            <>
              <section className="kpi-grid" style={{ marginTop: 12 }}>
                <article className="kpi-card good"><div className="kpi-title">Spend</div><div className="kpi-value">{fmtMoney(kpis.spend)}</div></article>
                <article className="kpi-card"><div className="kpi-title">Budget</div><div className="kpi-value">{kpis.budget ? fmtMoney(kpis.budget) : "--"}</div></article>
                <article className="kpi-card"><div className="kpi-title">Usage</div><div className="kpi-value">{kpis.usage == null ? "--" : `${kpis.usage.toFixed(1)}%`}</div></article>
                <article className="kpi-card"><div className="kpi-title">Pace</div><div className="kpi-value" style={{ fontSize: 24 }}>{kpis.pace.toUpperCase()}</div></article>
              </section>

              <section className="mid-grid" style={{ marginTop: 12 }}>
                <article className="panel">
                  <h3>My Accounts</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Platform</th>
                        <th>Status</th>
                        <th>Last Sync</th>
                      </tr>
                    </thead>
                    <tbody>
                      {accounts.map((a) => (
                        <tr key={a.id}>
                          <td>{a.name}</td>
                          <td>{a.platform}</td>
                          <td>{a.status}</td>
                          <td>{fmtDate(a.last_sync_at || a.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </article>

                <article className="panel">
                  <h3>Budget Snapshot</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Scope</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Period</th>
                      </tr>
                    </thead>
                    <tbody>
                      {budgets.map((b) => (
                        <tr key={b.id || `${b.client_id}-${b.account_id || "client"}`}>
                          <td>{b.scope}</td>
                          <td>{fmtMoney(Number(b.amount || 0))}</td>
                          <td>{b.status || "active"}</td>
                          <td>{b.start_date} to {b.end_date}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </article>
              </section>

              <section className="panel" style={{ marginTop: 12 }}>
                <h3>Recent Actions</h3>
                <div className="insights-list">
                  {actions.length ? actions.map((a) => (
                    <article key={a.id} className="insight-card">
                      <div className="insight-head">
                        <div className="insight-title">{a.title}</div>
                        <span className="badge">{a.status.toUpperCase()}</span>
                      </div>
                      <div className="insight-text">{a.title || `${a.action.toUpperCase()} / ${a.scope}`}</div>
                      <div className="insight-meta">{fmtDate(a.created_at)}</div>
                    </article>
                  )) : <div className="muted-note">No recent actions.</div>}
                </div>
              </section>
            </>
          ) : null}

          {activeTab === "reports" ? (
            <>
              <section className="kpi-grid" style={{ marginTop: 12 }}>
                <article className="kpi-card"><div className="kpi-title">Impressions</div><div className="kpi-value">{fmtNum(overview?.spend_summary?.impressions || 0)}</div></article>
                <article className="kpi-card"><div className="kpi-title">Clicks</div><div className="kpi-value">{fmtNum(overview?.spend_summary?.clicks || 0)}</div></article>
                <article className="kpi-card"><div className="kpi-title">Conversions</div><div className="kpi-value">{fmtNum(overview?.spend_summary?.conversions || 0)}</div></article>
                <article className="kpi-card"><div className="kpi-title">CTR</div><div className="kpi-value">{`${Number(overview?.spend_summary?.ctr || 0).toFixed(2)}%`}</div></article>
              </section>

              <section className="mid-grid" style={{ marginTop: 12 }}>
                <article className="panel">
                  <h3>Spend By Day</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Spend</th>
                      </tr>
                    </thead>
                    <tbody>
                      {spendByDate.map((row) => (
                        <tr key={row.date}>
                          <td>{row.date}</td>
                          <td>{fmtMoney(row.spend)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </article>

                <article className="panel">
                  <h3>Account Performance</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Account</th>
                        <th>Platform</th>
                        <th>Spend</th>
                        <th>CTR</th>
                        <th>CPC</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(overview?.breakdowns?.accounts || []).map((a) => (
                        <tr key={a.account_id}>
                          <td>{a.name}</td>
                          <td>{a.platform}</td>
                          <td>{fmtMoney(a.spend)}</td>
                          <td>{`${Number(a.ctr || 0).toFixed(2)}%`}</td>
                          <td>{fmtMoney(a.cpc)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </article>
              </section>

              <section className="panel" style={{ marginTop: 12 }}>
                <h3>Optimization Signals</h3>
                <div className="insights-list">
                  {insights.length ? insights.slice(0, 10).map((ins, idx) => (
                    <article key={`${ins.scope}-${ins.scope_id}-${idx}`} className="insight-card">
                      <div className="insight-head">
                        <div className="insight-title">{ins.title}</div>
                        <span className="badge">{ins.priority.toUpperCase()}</span>
                      </div>
                      <div className="insight-text">{ins.reason}</div>
                      <div className="insight-meta">{`Action: ${ins.action.toUpperCase()} / Score: ${ins.score}`}</div>
                    </article>
                  )) : <div className="muted-note">No optimization signals in this period.</div>}
                </div>
              </section>
            </>
          ) : null}

          {activeTab === "billing" ? (
            <>
              <section className="kpi-grid" style={{ marginTop: 12 }}>
                <article className="kpi-card"><div className="kpi-title">Budget</div><div className="kpi-value">{overview?.budget_summary?.budget == null ? "--" : fmtMoney(overview?.budget_summary?.budget)}</div></article>
                <article className="kpi-card"><div className="kpi-title">Spend</div><div className="kpi-value">{fmtMoney(overview?.budget_summary?.spend || 0)}</div></article>
                <article className="kpi-card"><div className="kpi-title">Remaining</div><div className="kpi-value">{overview?.budget_summary?.remaining == null ? "--" : fmtMoney(overview?.budget_summary?.remaining)}</div></article>
                <article className="kpi-card"><div className="kpi-title">Forecast</div><div className="kpi-value">{overview?.budget_summary?.forecast_spend == null ? "--" : fmtMoney(overview?.budget_summary?.forecast_spend)}</div></article>
              </section>

              <section className="panel" style={{ marginTop: 12 }}>
                <h3>Billing Scope</h3>
                <table>
                  <thead>
                    <tr>
                      <th>Scope</th>
                      <th>Account</th>
                      <th>Amount</th>
                      <th>Status</th>
                      <th>Start</th>
                      <th>End</th>
                    </tr>
                  </thead>
                  <tbody>
                    {budgets.map((b) => (
                      <tr key={b.id || `${b.client_id}-${b.account_id || "client"}`}>
                        <td>{b.scope}</td>
                        <td>{b.account_id || "--"}</td>
                        <td>{fmtMoney(Number(b.amount || 0))}</td>
                        <td>{b.status || "active"}</td>
                        <td>{b.start_date || "--"}</td>
                        <td>{b.end_date || "--"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section className="panel" style={{ marginTop: 12 }}>
                <h3>Pacing Status</h3>
                <div className="insights-list">
                  <article className="insight-card">
                    <div className="insight-head">
                      <div className="insight-title">Budget Pace</div>
                      <span className="badge">{String(overview?.budget_summary?.pace_status || "on_track").toUpperCase()}</span>
                    </div>
                    <div className="insight-text">
                      {`Usage: ${overview?.budget_summary?.usage_percent == null ? "--" : `${overview.budget_summary.usage_percent.toFixed(1)}%`}`}
                    </div>
                    <div className="insight-meta">
                      {`Delta: ${overview?.budget_summary?.pace_delta == null ? "--" : fmtMoney(overview.budget_summary.pace_delta)}`}
                    </div>
                  </article>
                </div>
              </section>
            </>
          ) : null}
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
