"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import { IntegrationsOverview, IntegrationProvider } from "../../lib/types";

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function providerLabel(v: string) {
  const p = (v || "").toLowerCase();
  if (p === "meta") return "Meta";
  if (p === "google") return "Google Ads";
  if (p === "tiktok") return "TikTok";
  return v;
}

function statusClass(status: IntegrationProvider["status"]) {
  if (status === "healthy") return "good";
  if (status === "warning") return "warn";
  return "bad";
}

export default function IntegrationsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [data, setData] = useState<IntegrationsOverview | null>(null);
  const [search, setSearch] = useState("");
  const [selectedProvider, setSelectedProvider] = useState("");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const d = await req<IntegrationsOverview>("/integrations/overview");
    setData(d);
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load integrations"));
  }, [ready, loadData]);

  const providers = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = data?.providers || [];
    if (!q) return rows;
    return rows.filter((p) => providerLabel(p.provider).toLowerCase().includes(q) || p.provider.toLowerCase().includes(q));
  }, [data, search]);

  useEffect(() => {
    if (!providers.length) {
      setSelectedProvider("");
      return;
    }
    if (!selectedProvider || !providers.some((p) => p.provider === selectedProvider)) {
      setSelectedProvider(providers[0].provider);
    }
  }, [providers, selectedProvider]);

  const selected = useMemo(() => providers.find((p) => p.provider === selectedProvider) || null, [providers, selectedProvider]);

  const recentEvents = useMemo(() => {
    const items = data?.events || [];
    if (!selected?.provider) return items.slice(0, 8);
    return items.filter((e) => e.provider === selected.provider).slice(0, 8);
  }, [data, selected]);

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="integrations" subtitle="Operational Readiness" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="integrations" />
              <div className="topbar-title">Integrations Hub</div>
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

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="kpi-grid" style={{ marginTop: 12 }}>
            <article className="kpi-card good">
              <div className="kpi-title">Connected Providers</div>
              <div className="kpi-value">{data?.summary.connected_providers ?? 0}</div>
            </article>
            <article className="kpi-card good">
              <div className="kpi-title">Healthy Connections</div>
              <div className="kpi-value">{data?.summary.healthy_connections ?? 0}</div>
            </article>
            <article className="kpi-card warn">
              <div className="kpi-title">Warnings</div>
              <div className="kpi-value">{data?.summary.warning_connections ?? 0}</div>
            </article>
            <article className="kpi-card bad">
              <div className="kpi-title">Critical Issues</div>
              <div className="kpi-value">{data?.summary.critical_issues ?? 0}</div>
            </article>
          </section>

          <section className="accounts-grid" style={{ marginTop: 12 }}>
            <article className="panel accounts-main">
              <div className="panel-head budgets-toolbar">
                <div>
                  <h3 style={{ margin: 0 }}>Active Providers</h3>
                  <div className="panel-subtitle">Manage and monitor active data connectors.</div>
                </div>
                <div className="session-controls">
                  <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search providers..." />
                  <button className="ghost-btn" disabled title="Coming soon">+ New Connection</button>
                </div>
              </div>

              <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
                <table className="budgets-table">
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Status</th>
                      <th>Sync Ready</th>
                      <th>Token</th>
                      <th>Scopes</th>
                      <th>Last Heartbeat</th>
                      <th>Linked Accounts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {providers.map((p) => (
                      <tr key={p.provider} className={selectedProvider === p.provider ? "selected" : ""} onClick={() => setSelectedProvider(p.provider)}>
                        <td><strong>{providerLabel(p.provider)}</strong></td>
                        <td>
                          <span className={`badge ${statusClass(p.status)}`}>{p.status.toUpperCase()}</span>
                          {p.status_reason ? <div className="muted-note" style={{ marginTop: 4 }}>{p.status_reason}</div> : null}
                        </td>
                        <td>
                          <span className={`badge ${p.sync_ready ? "good" : "warn"}`}>{p.sync_ready ? "READY" : "SETUP"}</span>
                          {p.sync_readiness_reason ? <div className="muted-note" style={{ marginTop: 4 }}>{p.sync_readiness_reason}</div> : null}
                        </td>
                        <td>{p.token_hint || "--"}</td>
                        <td>{p.scopes?.join(", ") || "--"}</td>
                        <td>{fmtDate(p.last_heartbeat_at)}</td>
                        <td>{p.linked_accounts_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="panel" style={{ marginTop: 12 }}>
                <div className="panel-head">
                  <h3 style={{ margin: 0 }}>System Events</h3>
                </div>
                <div style={{ marginTop: 8 }}>
                  {recentEvents.length ? recentEvents.map((e, idx) => (
                    <div key={`${e.provider}-${idx}-${e.occurred_at}`} style={{ padding: "8px 0", borderTop: idx ? "1px solid #e3e7ee" : "none" }}>
                      <strong>{e.title}</strong>
                      <div className="insight-text">{e.message}</div>
                      <div className="muted-note">{fmtDate(e.occurred_at)}</div>
                    </div>
                  )) : <div className="muted-note">No events yet</div>}
                </div>
              </div>
            </article>

            <aside className="panel accounts-detail">
              {!selected ? (
                <div className="muted-note">Select provider to inspect details.</div>
              ) : (
                <>
                  <div className="budgets-detail-head">
                    <div>
                      <div className="kpi-title">Detail Panel</div>
                      <h3 style={{ margin: 0 }}>{providerLabel(selected.provider)}</h3>
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="kpi-title">Auth Status</div>
                    <div className="detail-grid">
                      <div className="detail-item"><div className="detail-k">Auth State</div><div className="detail-v">{selected.auth_state}</div></div>
                      <div className="detail-item"><div className="detail-k">Sync Ready</div><div className="detail-v">{selected.sync_ready ? "Yes" : "No"}</div></div>
                      <div className="detail-item"><div className="detail-k">Identity Linked Users</div><div className="detail-v">{selected.identity_linked_users}</div></div>
                      <div className="detail-item"><div className="detail-k">Last Success</div><div className="detail-v">{fmtDate(selected.last_successful_sync_at)}</div></div>
                      <div className="detail-item"><div className="detail-k">Last Error</div><div className="detail-v">{fmtDate(selected.last_error_time)}</div></div>
                      <div className="detail-item"><div className="detail-k">Clients Affected</div><div className="detail-v">{selected.affected_clients_count}</div></div>
                    </div>
                    <div style={{ marginTop: 10 }}>
                      <div className="kpi-title">Connection Sources</div>
                      <div className="muted-note">{selected.connection_sources?.length ? selected.connection_sources.join(", ") : "--"}</div>
                    </div>
                    <div style={{ marginTop: 10 }}>
                      <div className="kpi-title">Missing Requirements</div>
                      <div className="muted-note">{selected.missing_requirements?.length ? selected.missing_requirements.join(", ") : "None"}</div>
                    </div>
                    {selected.last_error_safe ? (
                      <div className="alert-card high" style={{ marginTop: 10 }}>
                        <div className="alert-priority high">DIAGNOSTIC</div>
                        <div className="insight-text" style={{ color: "#9e2b2b", marginTop: 8 }}>{selected.last_error_safe}</div>
                      </div>
                    ) : null}
                  </div>

                  <div className="budgets-detail-actions">
                    <button
                      className="primary-btn"
                      onClick={() => push("Reconnect flow is planned for next auth step", "info")}
                      disabled={!selected.reconnect_available}
                    >
                      Reconnect
                    </button>
                    <button className="ghost-btn" onClick={() => push("Use Accounts screen: Sync All / Retry", "info")}>Run Sync Probe</button>
                  </div>
                </>
              )}
            </aside>
          </section>
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
