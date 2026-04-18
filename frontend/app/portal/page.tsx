"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson, getQuery } from "../../lib/api";
import { AdAccount, AuthMeResponse, Budget, OperationalAction, Overview, SessionContext } from "../../lib/types";

function fmtMoney(v: number | null | undefined) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v || 0);
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

export default function ClientPortalPage() {
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
    const context = (await loadContext()) || ctx;
    if (!context?.valid) {
      setWarning("Invalid or expired session. Please sign in again.");
      return;
    }
    if (context.role !== "client") {
      setWarning("This portal is client-only. Use agency console for admin/agency roles.");
      return;
    }

    const available = context.accessible_client_ids || [];
    const cid = selectedClientId || available[0] || "";
    setSelectedClientId(cid);
    if (!cid) {
      setWarning("No assigned client scope for this session.");
      return;
    }

    const r = dateRange(periodDays);
    const q = getQuery({ client_id: cid, date_from: r.from, date_to: r.to });
    const [ov, acc, bgs, acts] = await Promise.all([
      req<Overview>(`/insights/overview${q}`),
      req<{ items: AdAccount[] }>(`/ad-accounts${getQuery({ client_id: cid, status: "active" })}`),
      req<{ items: Budget[] }>(`/budgets${getQuery({ client_id: cid, status: "active", date_from: r.from, date_to: r.to })}`),
      req<OperationalAction[]>(`/insights/operational/actions${getQuery({ client_id: cid })}`),
    ]);

    setOverview(ov);
    setAccounts(acc.items || []);
    setBudgets(bgs.items || []);
    setActions(Array.isArray(acts) ? acts.slice(0, 10) : []);
    setWarning("");
  }, [ctx, loadContext, periodDays, req, selectedClientId]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load portal"));
  }, [ready, loadData]);

  const kpis = useMemo(() => {
    const spend = Number(overview?.spend_summary?.spend || 0);
    const budget = Number(overview?.budget_summary?.budget || 0);
    const usage = overview?.budget_summary?.usage_percent;
    const pace = String(overview?.budget_summary?.pace_status || "on_track");
    return { spend, budget, usage, pace };
  }, [overview]);

  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">Editorial Rigor</div>
          <div className="panel-subtitle">Client Portal</div>
          <nav className="menu">
            <Link className="menu-item active" href="/portal">Overview</Link>
            <a className="menu-item" href="#" aria-disabled>Reports</a>
            <a className="menu-item" href="#" aria-disabled>Billing</a>
          </nav>
          <div className="sidebar-footer">
            <a className="menu-item" href="#">Documentation</a>
            <a className="menu-item" href="#">Support</a>
            <Link className="menu-item" href="/">Open Agency Console</Link>
          </div>
        </aside>

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <div className="topbar-title">Client Overview</div>
              <div className="panel-subtitle">Read-only performance and budget tracking</div>
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

          <section className="kpi-grid" style={{ marginTop: 12 }}>
            <article className="kpi-card good"><div className="kpi-title">Spend</div><div className="kpi-value">{fmtMoney(kpis.spend)}</div></article>
            <article className="kpi-card"><div className="kpi-title">Budget</div><div className="kpi-value">{kpis.budget ? fmtMoney(kpis.budget) : "—"}</div></article>
            <article className="kpi-card"><div className="kpi-title">Usage</div><div className="kpi-value">{kpis.usage == null ? "—" : `${kpis.usage.toFixed(1)}%`}</div></article>
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
                      <td>{b.start_date} → {b.end_date}</td>
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
                  <div className="insight-text">{a.title || `${a.action.toUpperCase()} • ${a.scope}`}</div>
                  <div className="insight-meta">{fmtDate(a.created_at)}</div>
                </article>
              )) : <div className="muted-note">No recent actions.</div>}
            </div>
          </section>
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
