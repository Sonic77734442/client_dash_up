"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson } from "../../../lib/api";
import { AlertOut, AuthMeResponse } from "../../../lib/types";

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function badgeClass(v: string) {
  if (v === "critical" || v === "high") return "bad";
  if (v === "medium") return "warn";
  return "good";
}

export default function PlatformAlertsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [status, setStatus] = useState<"open" | "acked" | "resolved" | "all">("open");
  const [severity, setSeverity] = useState<"" | "critical" | "high" | "medium" | "low">("");
  const [provider, setProvider] = useState("");
  const [alerts, setAlerts] = useState<AlertOut[]>([]);

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const me = await req<AuthMeResponse>("/auth/me");
    if (me.user.role !== "admin") {
      throw new Error("Admin access required");
    }
    const q = new URLSearchParams({ status, limit: "200" });
    if (severity) q.set("severity", severity);
    if (provider.trim()) q.set("provider", provider.trim().toLowerCase());
    const rows = await req<AlertOut[]>(`/alerts?${q.toString()}`);
    setAlerts(rows || []);
  }, [req, provider, severity, status]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load alerts"));
  }, [ready, loadData]);

  async function ackAlert(alertId: string) {
    try {
      await req<AlertOut>(`/alerts/${alertId}/ack`, { method: "POST" });
      await loadData();
      push("Alert acknowledged", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Acknowledge failed", "error");
    }
  }

  async function resolveAlert(alertId: string) {
    try {
      await req<AlertOut>(`/alerts/${alertId}/resolve`, { method: "POST" });
      await loadData();
      push("Alert resolved", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Resolve failed", "error");
    }
  }

  const summary = useMemo(() => {
    return {
      total: alerts.length,
      critical: alerts.filter((x) => x.severity === "critical").length,
      high: alerts.filter((x) => x.severity === "high").length,
      open: alerts.filter((x) => x.status === "open").length,
    };
  }, [alerts]);

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="platform_admin" subtitle="Platform Administration" />
        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="platform_admin" />
              <div className="topbar-title">Platform Alerts</div>
            </div>
            <div className="session-controls">
              <a className="ghost-btn" href="/platform/users">Users</a>
              <a className="ghost-btn" href="/platform/agencies">Agencies</a>
              {tokenLoginEnabled ? (
                <>
                  <input value={session.apiBase} onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))} placeholder="API base" />
                  <input type="password" value={session.token} onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))} placeholder="Session token" />
                  <button
                    className="ghost-btn"
                    onClick={async () => {
                      const next = { apiBase: session.apiBase.trim().replace(/\/$/, "") || defaultApiBase, token: session.token.trim() };
                      persist(next);
                      setSession(next);
                      await loadData();
                      push("Session saved", "success");
                    }}
                  >
                    Save
                  </button>
                </>
              ) : null}
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="kpi-grid" style={{ marginTop: 12 }}>
            <article className="kpi-card"><div className="kpi-title">Total</div><div className="kpi-value">{summary.total}</div></article>
            <article className="kpi-card bad"><div className="kpi-title">Critical</div><div className="kpi-value">{summary.critical}</div></article>
            <article className="kpi-card bad"><div className="kpi-title">High</div><div className="kpi-value">{summary.high}</div></article>
            <article className="kpi-card warn"><div className="kpi-title">Open</div><div className="kpi-value">{summary.open}</div></article>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="chip-row" style={{ marginTop: 0 }}>
              <label>
                Status
                <select value={status} onChange={(e) => setStatus(e.target.value as "open" | "acked" | "resolved" | "all")}>
                  <option value="open">open</option>
                  <option value="acked">acked</option>
                  <option value="resolved">resolved</option>
                  <option value="all">all</option>
                </select>
              </label>
              <label>
                Severity
                <select value={severity} onChange={(e) => setSeverity(e.target.value as "" | "critical" | "high" | "medium" | "low")}>
                  <option value="">all</option>
                  <option value="critical">critical</option>
                  <option value="high">high</option>
                  <option value="medium">medium</option>
                  <option value="low">low</option>
                </select>
              </label>
              <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="provider (google/meta/tiktok)" />
              <button className="ghost-btn" onClick={() => void loadData()}>Refresh</button>
            </div>

            <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
              <table className="budgets-table">
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Status</th>
                    <th>Code</th>
                    <th>Provider</th>
                    <th>Message</th>
                    <th>Occurrences</th>
                    <th>Last Seen</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((a) => (
                    <tr key={a.id}>
                      <td><span className={`badge ${badgeClass(a.severity)}`}>{a.severity}</span></td>
                      <td>{a.status}</td>
                      <td>{a.code}</td>
                      <td>{a.provider || "--"}</td>
                      <td>{a.message}</td>
                      <td>{a.occurrences}</td>
                      <td>{fmtDate(a.last_seen_at)}</td>
                      <td>
                        <button className="ghost-btn" onClick={() => void ackAlert(a.id)} disabled={a.status !== "open"}>
                          Ack
                        </button>
                        <button className="ghost-btn" onClick={() => void resolveAlert(a.id)} disabled={a.status === "resolved"}>
                          Resolve
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!alerts.length ? (
                    <tr>
                      <td colSpan={8} className="muted-note">No alerts.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>
        </main>
      </div>
      <ToastHost toasts={toasts} />
    </>
  );
}
