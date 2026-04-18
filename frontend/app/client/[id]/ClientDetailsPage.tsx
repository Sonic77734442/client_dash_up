"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { useSession } from "../../../hooks/useSession";
import { useOperationalActions } from "../../../hooks/useOperationalActions";
import { useToast } from "../../../hooks/useToast";
import { ToastHost } from "../../../components/ToastHost";
import { fetchJson, getQuery } from "../../../lib/api";
import { AdAccount, Budget, ClientOut, OperationalAction, Overview } from "../../../lib/types";

function fmtMoney(v: number | null | undefined) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v || 0);
}

function dateRange(periodDays: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (periodDays - 1));
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

export default function ClientDetailsPage({ clientId }: { clientId: string }) {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const { session, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();
  const { executeAction, listActions } = useOperationalActions(session.apiBase, session.token);

  const [periodDays, setPeriodDays] = useState(30);
  const [client, setClient] = useState<ClientOut | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [actions, setActions] = useState<OperationalAction[]>([]);
  const [warning, setWarning] = useState("");

  const request = async <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init);

  useEffect(() => {
    if (!ready || !session.token || !clientId) return;
    const run = async () => {
      try {
        setWarning("");
        const r = dateRange(periodDays);
        const q = getQuery({ client_id: clientId, date_from: r.from, date_to: r.to });
        const [c, ov, acc, bg, ac] = await Promise.all([
          request<ClientOut>(`/clients/${clientId}`),
          request<Overview>(`/insights/overview${q}`),
          request<{ items: AdAccount[] }>(`/ad-accounts${getQuery({ client_id: clientId, status: "active" })}`),
          request<{ items: Budget[] }>(`/budgets${getQuery({ client_id: clientId, status: "active", date_from: r.from, date_to: r.to })}`),
          listActions({ clientId }),
        ]);
        setClient(c);
        setOverview(ov);
        setAccounts(acc.items || []);
        setBudgets(bg.items || []);
        setActions(Array.isArray(ac) ? ac : []);
      } catch (e) {
        setWarning(e instanceof Error ? e.message : "Failed to load client details");
      }
    };
    void run();
  }, [ready, session.token, session.apiBase, clientId, periodDays, listActions]);

  const clientBudget = useMemo(() => {
    const rows = (budgets || []).filter((b) => b.scope === "client");
    return rows[0] || null;
  }, [budgets]);

  const accountPerfMap = useMemo(() => {
    const map = new Map<string, { spend: number; clicks: number; ctr: number; cpc: number; conversions: number }>();
    for (const row of overview?.breakdowns?.accounts || []) {
      map.set(row.account_id, {
        spend: Number(row.spend || 0),
        clicks: Number(row.clicks || 0),
        ctr: Number(row.ctr || 0),
        cpc: Number(row.cpc || 0),
        conversions: Number(row.conversions || 0),
      });
    }
    return map;
  }, [overview]);

  async function runQuickAction(scope: "client" | "account", action: "cap" | "review" | "scale", accountId?: string) {
    try {
      await executeAction({
        action,
        scope,
        scope_id: scope === "client" ? clientId : (accountId || ""),
        client_id: clientId,
        account_id: scope === "account" ? accountId : undefined,
        title: `${action.toUpperCase()} for ${scope === "client" ? (client?.name || "client") : `account ${accountId?.slice(0, 8)}`}`,
        reason: "Triggered from Client Details quick actions",
        metrics: {},
      });
      const ac = await listActions({ clientId });
      setActions(Array.isArray(ac) ? ac : []);
      push(`Action queued: ${action.toUpperCase()} (${scope})`, "success");
    } catch (e) {
      setWarning(e instanceof Error ? e.message : "Action failed");
      push("Action failed", "error");
    }
  }

  return (
    <div className="app-shell">
      <AppSidebar active="clients" subtitle="Client Workspace" />

      <main className="content">
        <header className="topbar">
          <div className="topbar-left">
            <AppTopTabs active="clients" />
            <div className="panel-subtitle">
              <Link href="/" style={{ color: "#5e718c", textDecoration: "none" }}>Dashboard</Link>
              {" / "}
              <Link href="/" style={{ color: "#5e718c", textDecoration: "none" }}>Client Operations</Link>
              {" / "}
              <strong>{client?.name || "Client"}</strong>
            </div>
            <div className="topbar-title">{client?.name || "Client Details"}</div>
            <div className="panel-subtitle">Dedicated client workspace</div>
          </div>
          <div className="session-controls">
            <label>
              <span style={{ fontSize: 12, color: "#738093", fontWeight: 700, marginRight: 8 }}>Period</span>
              <select value={String(periodDays)} onChange={(e) => setPeriodDays(Number(e.target.value))}>
                <option value="7">Last 7 Days</option>
                <option value="30">Last 30 Days</option>
                <option value="90">Last 90 Days</option>
              </select>
            </label>
          </div>
        </header>

        <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

        <section className="kpi-grid" style={{ marginTop: 0 }}>
          <article className="kpi-card good">
            <div className="kpi-head"><div className="kpi-title">Spend</div></div>
            <div className="kpi-value">{fmtMoney(overview?.spend_summary?.spend || 0)}</div>
          </article>
          <article className="kpi-card">
            <div className="kpi-head"><div className="kpi-title">Budget</div></div>
            <div className="kpi-value">{overview?.budget_summary?.budget == null ? "—" : fmtMoney(overview?.budget_summary?.budget || 0)}</div>
          </article>
          <article className="kpi-card">
            <div className="kpi-head"><div className="kpi-title">Usage %</div></div>
            <div className="kpi-value">{overview?.budget_summary?.usage_percent == null ? "—" : `${overview?.budget_summary?.usage_percent.toFixed(1)}%`}</div>
          </article>
          <article className="kpi-card">
            <div className="kpi-head"><div className="kpi-title">Pace</div></div>
            <div className="kpi-value" style={{ fontSize: 26 }}>{String(overview?.budget_summary?.pace_status || "on_track").toUpperCase()}</div>
          </article>
        </section>

        <section className="mid-grid">
          <article className="panel">
            <h3>Per-account performance</h3>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Platform</th>
                  <th>Spend</th>
                  <th>Clicks</th>
                  <th>CTR</th>
                  <th>CPC</th>
                  <th>Conv.</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}</td>
                    <td>{a.platform.toUpperCase()}</td>
                    <td>{fmtMoney(accountPerfMap.get(a.id)?.spend || 0)}</td>
                    <td>{accountPerfMap.get(a.id)?.clicks || 0}</td>
                    <td>{((accountPerfMap.get(a.id)?.ctr || 0) * 100).toFixed(1)}%</td>
                    <td>{fmtMoney(accountPerfMap.get(a.id)?.cpc || 0)}</td>
                    <td>{accountPerfMap.get(a.id)?.conversions || 0}</td>
                    <td>{a.status}</td>
                    <td>
                      <div className="alert-actions" style={{ marginTop: 0 }}>
                        <button className="mini-btn" onClick={() => void runQuickAction("account", "cap", a.id)}>CAP</button>
                        <button className="mini-btn" onClick={() => void runQuickAction("account", "review", a.id)}>REVIEW</button>
                        <button className="mini-btn" onClick={() => void runQuickAction("account", "scale", a.id)}>SCALE</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </article>

          <article className="panel">
            <h3>Budget Timeline</h3>
            <div className="insight-text" style={{ marginTop: 8 }}>
              Budget Source: {clientBudget ? "CLIENT" : "NONE"}
            </div>
            <div className="insight-text">Budget: {clientBudget ? fmtMoney(Number(clientBudget.amount || 0)) : "—"}</div>
            <div className="insight-text">Forecast: {overview?.budget_summary?.forecast_spend == null ? "—" : fmtMoney(overview?.budget_summary?.forecast_spend)}</div>
            <div className="insight-text">Remaining: {overview?.budget_summary?.remaining == null ? "—" : fmtMoney(overview?.budget_summary?.remaining)}</div>
          </article>
        </section>

        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-head" style={{ marginBottom: 8, display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>Client Quick Actions</h3>
            <div className="alert-actions" style={{ marginTop: 0 }}>
              <button className="mini-btn" onClick={() => void runQuickAction("client", "cap")}>CAP CLIENT</button>
              <button className="mini-btn" onClick={() => void runQuickAction("client", "review")}>REVIEW CLIENT</button>
              <button className="mini-btn" onClick={() => void runQuickAction("client", "scale")}>SCALE CLIENT</button>
            </div>
          </div>
        </section>

        <section className="panel" style={{ marginTop: 12 }}>
          <h3>Recent Actions</h3>
          {!actions.length ? (
            <div className="muted-note">No actions yet for this client.</div>
          ) : (
            <div className="side-stack" style={{ marginTop: 10 }}>
              {actions.slice(0, 8).map((x) => (
                <div key={x.id} className="action-row timeline-item">
                  <div className="action-row-head">
                    <div className="action-title">{String(x.action || "").toUpperCase()} • {String(x.scope || "").toUpperCase()}</div>
                    <span className={`status-pill ${String(x.status || "queued")}`}>{String(x.status || "queued").toUpperCase()}</span>
                  </div>
                  <div className="action-meta">{x.title || "--"}</div>
                  <div className="action-meta">{new Date(x.created_at).toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
      <ToastHost toasts={toasts} />
    </div>
  );
}
