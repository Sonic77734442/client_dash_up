"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson, getQuery } from "../../../lib/api";
import { AuthMeResponse, Budget, Overview, SessionContext } from "../../../lib/types";

function dateRange(periodDays: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (periodDays - 1));
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

function fmtMoney(v: number | null | undefined) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v || 0);
}

export default function ClientPortalBillingPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [periodDays, setPeriodDays] = useState(30);
  const [warning, setWarning] = useState("");
  const [ctx, setCtx] = useState<SessionContext | null>(null);
  const [selectedClientId, setSelectedClientId] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);

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
    const [ov, bgs] = await Promise.all([
      req<Overview>(`/insights/overview${q}`),
      req<{ items: Budget[] }>(`/budgets${getQuery({ client_id: cid, status: "active", date_from: r.from, date_to: r.to })}`),
    ]);
    setOverview(ov);
    setBudgets(bgs.items || []);
    setWarning("");
  }, [ctx, loadContext, periodDays, req, selectedClientId]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load billing"));
  }, [ready, loadData]);

  const summary = useMemo(() => {
    const spend = Number(overview?.budget_summary?.spend || 0);
    const budget = Number(overview?.budget_summary?.budget || 0);
    const remaining = overview?.budget_summary?.remaining;
    const usage = overview?.budget_summary?.usage_percent;
    return { spend, budget, remaining, usage };
  }, [overview]);

  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">Editorial Rigor</div>
          <div className="panel-subtitle">Client Portal</div>
          <nav className="menu">
            <Link className="menu-item" href="/portal">Overview</Link>
            <Link className="menu-item" href="/portal/reports">Reports</Link>
            <Link className="menu-item active" href="/portal/billing">Billing</Link>
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
              <div className="topbar-title">Billing & Budgets</div>
              <div className="panel-subtitle">Client budget status and active budget records</div>
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
            <article className="kpi-card good"><div className="kpi-title">Spend</div><div className="kpi-value">{fmtMoney(summary.spend)}</div></article>
            <article className="kpi-card"><div className="kpi-title">Budget</div><div className="kpi-value">{summary.budget ? fmtMoney(summary.budget) : "-"}</div></article>
            <article className="kpi-card"><div className="kpi-title">Remaining</div><div className="kpi-value">{summary.remaining == null ? "-" : fmtMoney(summary.remaining)}</div></article>
            <article className="kpi-card"><div className="kpi-title">Usage</div><div className="kpi-value">{summary.usage == null ? "-" : `${summary.usage.toFixed(1)}%`}</div></article>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <h3>Active Budgets</h3>
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
                  <tr key={b.id || `${b.client_id}-${b.account_id || "client"}-${b.start_date || ""}`}>
                    <td>{b.scope}</td>
                    <td>{fmtMoney(Number(b.amount || 0))}</td>
                    <td>{b.status || "active"}</td>
                    <td>{b.start_date || "-"} to {b.end_date || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!budgets.length ? <div className="muted-note">No active budget records for selected period.</div> : null}
          </section>
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
