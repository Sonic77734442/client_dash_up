"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import {
  AdAccount,
  AdAccountDiscoverResponse,
  AdAccountSyncJob,
  AuthMeResponse,
  ClientOut,
  IntegrationsOverview,
  IntegrationProvider,
} from "../../lib/types";

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function statusClass(status: string) {
  return status === "success" ? "good" : "bad";
}

function providerLabel(v: string) {
  const p = (v || "").toLowerCase();
  if (p === "meta" || p === "facebook") return "Meta";
  if (p === "google" || p === "google_ads") return "Google Ads";
  if (p === "tiktok" || p === "tt") return "TikTok";
  return v;
}

function providerStatusClass(status: IntegrationProvider["status"]) {
  if (status === "healthy") return "good";
  if (status === "warning") return "warn";
  return "bad";
}

function asSyncPlatform(provider: string): "meta" | "google" | "tiktok" | null {
  const p = (provider || "").toLowerCase().trim();
  if (p === "meta" || p === "facebook") return "meta";
  if (p === "google" || p === "google_ads") return "google";
  if (p === "tiktok" || p === "tt") return "tiktok";
  return null;
}

function safeErrorMessage(raw?: string | null) {
  const msg = String(raw || "").toLowerCase();
  if (!msg) return "";
  if (msg.includes("expired") || msg.includes("unauthorized") || msg.includes("invalid token")) {
    return "Authentication expired or invalid. Reconnect provider.";
  }
  if (msg.includes("scope") || msg.includes("permission") || msg.includes("forbidden") || msg.includes("access")) {
    return "Insufficient permissions for required API scopes.";
  }
  if (msg.includes("rate") || msg.includes("throttl") || msg.includes("quota")) {
    return "Provider is rate-limiting requests. Retry later.";
  }
  if (msg.includes("credential") || msg.includes("not set")) {
    return "Provider credentials are missing or incomplete.";
  }
  if (msg.includes("customer") && msg.includes("not found")) {
    return "Google account is not accessible in the connected MCC/customer scope.";
  }
  if (msg.includes("manager") && msg.includes("hierarchy")) {
    return "Google account is outside current manager hierarchy. Check MCC access/link.";
  }
  if (msg.includes("developer token")) {
    return "Google developer token is missing/invalid for current credentials.";
  }
  return "Sync failed. Check provider diagnostics and retry.";
}

export default function SyncMonitorPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [jobs, setJobs] = useState<AdAccountSyncJob[]>([]);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationsOverview | null>(null);

  const [provider, setProvider] = useState("all");
  const [status, setStatus] = useState<"all" | "success" | "error">("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [syncLoading, setSyncLoading] = useState(false);
  const [discoverClientId, setDiscoverClientId] = useState("");
  const [currentRole, setCurrentRole] = useState<"admin" | "agency" | "client" | "unknown">("unknown");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const [jobRows, accRows, clientRows, integrationsRows] = await Promise.all([
      req<{ items: AdAccountSyncJob[] }>(`/ad-accounts/sync/jobs?status=all&limit=500`),
      req<{ items: AdAccount[] }>("/ad-accounts?status=all"),
      req<{ items: ClientOut[] }>("/clients?status=all"),
      req<IntegrationsOverview>("/integrations/overview"),
    ]);
    const me = await req<AuthMeResponse>("/auth/me");
    setCurrentRole(me?.user?.role || "unknown");
    setJobs(jobRows.items || []);
    setAccounts(accRows.items || []);
    setClients(clientRows.items || []);
    setIntegrations(integrationsRows);
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load sync monitor"));
  }, [ready, loadData]);

  useEffect(() => {
    if (!discoverClientId && clients.length === 1) {
      setDiscoverClientId(clients[0].id);
    }
  }, [discoverClientId, clients]);

  const accountMap = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);
  const clientMap = useMemo(() => new Map(clients.map((c) => [c.id, c.name])), [clients]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return jobs
      .filter((j) => (provider === "all" ? true : j.provider === provider))
      .filter((j) => (status === "all" ? true : j.status === status))
      .filter((j) => {
        if (!q) return true;
        const acc = accountMap.get(j.ad_account_id);
        const clientName = acc ? clientMap.get(acc.client_id) || "" : "";
        const hay = `${j.provider} ${j.status} ${j.ad_account_id} ${acc?.name || ""} ${clientName}`.toLowerCase();
        return hay.includes(q);
      })
      .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime());
  }, [jobs, provider, status, search, accountMap, clientMap]);

  useEffect(() => {
    if (!rows.length) {
      setSelectedId("");
      return;
    }
    if (!selectedId || !rows.some((r) => r.id === selectedId)) {
      setSelectedId(rows[0].id);
    }
  }, [rows, selectedId]);

  const selected = useMemo(() => rows.find((r) => r.id === selectedId) || null, [rows, selectedId]);
  const providerMap = useMemo(() => {
    const map = new Map<string, IntegrationProvider>();
    for (const p of integrations?.providers || []) {
      map.set((p.provider || "").toLowerCase(), p);
    }
    return map;
  }, [integrations]);
  const selectedProviderState = useMemo(() => {
    if (!selected?.provider) return null;
    return providerMap.get((selected.provider || "").toLowerCase()) || null;
  }, [providerMap, selected?.provider]);

  const providerOptions = useMemo(() => ["all", ...Array.from(new Set(jobs.map((j) => j.provider))).sort()], [jobs]);

  function connectProvider(providerName: "google" | "facebook") {
    if (currentRole === "client") {
      push("Provider connection is available only for agency/admin users", "info");
      return;
    }
    const base = session.apiBase.trim().replace(/\/$/, "") || defaultApiBase;
    localStorage.setItem("ops_api_base", base);
    window.location.href = `${base}/auth/${providerName}/start?next=/sync-monitor`;
  }

  async function runSync(opts?: { platform?: "meta" | "google" | "tiktok"; accountId?: string }) {
    if (currentRole === "client") {
      push("Sync is available only for agency/admin users", "info");
      return;
    }
    try {
      setSyncLoading(true);
      const payload: Record<string, unknown> = { force: true };
      if (discoverClientId) payload.client_id = discoverClientId;
      if (opts?.platform) payload.platform = opts.platform;
      if (opts?.accountId) payload.account_ids = [opts.accountId];
      await req("/ad-accounts/sync/run", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const scope = opts?.accountId ? "account" : opts?.platform || "all providers";
      push(`Sync queued (${scope})`, "success");
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Sync failed";
      push(msg, "error");
    } finally {
      setSyncLoading(false);
    }
  }

  async function discoverAccounts(providerName?: "meta" | "google" | "tiktok") {
    if (currentRole === "client") {
      push("Discovery is available only for agency/admin users", "info");
      return;
    }
    if (!discoverClientId) {
      push("Select client for imported accounts", "info");
      return;
    }
    try {
      setSyncLoading(true);
      const payload: Record<string, unknown> = { client_id: discoverClientId, upsert_existing: true };
      if (providerName) payload.provider = providerName;
      const res = await req<AdAccountDiscoverResponse>("/ad-accounts/discover", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const failCount = Object.keys(res.providers_failed || {}).length;
      const summary = `Discover: +${res.created} new, ${res.updated} updated, ${res.skipped} skipped`;
      push(failCount ? `${summary} (${failCount} provider errors)` : summary, failCount ? "info" : "success");
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Discover failed";
      push(msg, "error");
    } finally {
      setSyncLoading(false);
    }
  }

  async function retrySelected() {
    if (!selected?.ad_account_id) {
      push("Select a sync job first", "info");
      return;
    }
    await runSync({ accountId: selected.ad_account_id });
  }

  const kpis = useMemo(() => {
    const total = rows.length;
    const success = rows.filter((r) => r.status === "success").length;
    const error = rows.filter((r) => r.status === "error").length;
    const uniqueAccounts = new Set(rows.map((r) => r.ad_account_id)).size;
    return { total, success, error, uniqueAccounts };
  }, [rows]);

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="sync_monitor" subtitle="Operational Readiness" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="sync_monitor" />
              <div className="topbar-title">Sync Monitor</div>
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
            <article className="kpi-card"><div className="kpi-title">Total Jobs</div><div className="kpi-value">{kpis.total}</div></article>
            <article className="kpi-card good"><div className="kpi-title">Success</div><div className="kpi-value">{kpis.success}</div></article>
            <article className="kpi-card bad"><div className="kpi-title">Errors</div><div className="kpi-value">{kpis.error}</div></article>
            <article className="kpi-card"><div className="kpi-title">Accounts Involved</div><div className="kpi-value">{kpis.uniqueAccounts}</div></article>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>Provider Connection State</h3>
                <div className="panel-subtitle">
                  Connect provider once (agency/admin) and accounts are discovered automatically. No manual MCC/BM input for clients.
                </div>
              </div>
              <div className="session-controls">
                <select
                  value={discoverClientId}
                  onChange={(e) => setDiscoverClientId(e.target.value)}
                  title="Target client for imported accounts"
                >
                  <option value="">Select client</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <button className="ghost-btn" onClick={() => void discoverAccounts()} disabled={syncLoading}>
                  Discover Accounts
                </button>
                <button className="ghost-btn" onClick={() => connectProvider("google")}>Connect Google</button>
                <button className="ghost-btn" onClick={() => connectProvider("facebook")}>Connect Facebook</button>
                <button className="primary-btn" onClick={() => void runSync()} disabled={syncLoading}>Sync All</button>
              </div>
            </div>
            {currentRole === "client" ? (
              <div className="muted-note" style={{ marginTop: 8 }}>
                Client role is read-only here. Provider connect/discovery/sync is managed by agency/admin.
              </div>
            ) : null}
            <div className="kpi-grid" style={{ marginTop: 10 }}>
              {(integrations?.providers || []).map((p) => (
                <article key={p.provider} className={`kpi-card ${providerStatusClass(p.status)}`}>
                  <div className="kpi-title">{providerLabel(p.provider)}</div>
                  <div className="kpi-value" style={{ fontSize: 22 }}>{p.sync_ready ? "READY" : "SETUP"}</div>
                  <div className="muted-note">
                    Source: {p.connection_sources?.length ? p.connection_sources.join(", ") : "not configured"}
                  </div>
                  <div className="muted-note">
                    Missing: {p.missing_requirements?.length ? p.missing_requirements.join(", ") : "none"}
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <button
                      className="ghost-btn"
                      onClick={() => {
                        const platform = asSyncPlatform(p.provider);
                        if (!platform) {
                          push("Provider is not supported for sync run", "info");
                          return;
                        }
                        void runSync({ platform });
                      }}
                      disabled={syncLoading}
                    >
                      Sync {providerLabel(p.provider)}
                    </button>
                    <button
                      className="ghost-btn"
                      onClick={() => {
                        const platform = asSyncPlatform(p.provider);
                        if (!platform) {
                          push("Provider is not supported for account discovery", "info");
                          return;
                        }
                        void discoverAccounts(platform);
                      }}
                      disabled={syncLoading}
                    >
                      Discover {providerLabel(p.provider)}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="accounts-grid" style={{ marginTop: 12 }}>
            <article className="panel accounts-main">
              <div className="chip-row" style={{ marginTop: 0 }}>
                <label>
                  Provider
                  <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                    {providerOptions.map((p) => (
                      <option key={p} value={p}>{p === "all" ? "All" : p}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Status
                  <select value={status} onChange={(e) => setStatus(e.target.value as "all" | "success" | "error")}>
                    <option value="all">All</option>
                    <option value="success">Success</option>
                    <option value="error">Error</option>
                  </select>
                </label>
                <input className="clientops-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search provider/account/client" />
                <button className="ghost-btn" onClick={() => void loadData()}>Refresh</button>
              </div>

              <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
                <table className="budgets-table">
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Status</th>
                      <th>Account</th>
                      <th>Client</th>
                      <th>Started</th>
                      <th>Records</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const acc = accountMap.get(r.ad_account_id);
                      const clientName = acc ? clientMap.get(acc.client_id) || "--" : "--";
                      return (
                        <tr key={r.id} className={selectedId === r.id ? "selected" : ""} onClick={() => setSelectedId(r.id)}>
                          <td>{r.provider}</td>
                          <td><span className={`badge ${statusClass(r.status)}`}>{r.status.toUpperCase()}</span></td>
                          <td>{acc?.name || r.ad_account_id.slice(0, 8)}</td>
                          <td>{clientName}</td>
                          <td>{fmtDate(r.started_at)}</td>
                          <td>{r.records_synced}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </article>

            <aside className="panel accounts-detail">
              {!selected ? (
                <div className="muted-note">Select sync job to inspect details.</div>
              ) : (
                <>
                  <div className="budgets-detail-head">
                    <div>
                      <div className="kpi-title">Detail Panel</div>
                      <h3 style={{ margin: 0 }}>{selected.provider} • {selected.status.toUpperCase()}</h3>
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="detail-grid">
                      <div className="detail-item"><div className="detail-k">Started</div><div className="detail-v">{fmtDate(selected.started_at)}</div></div>
                      <div className="detail-item"><div className="detail-k">Finished</div><div className="detail-v">{fmtDate(selected.finished_at)}</div></div>
                      <div className="detail-item"><div className="detail-k">Records Synced</div><div className="detail-v">{selected.records_synced}</div></div>
                      <div className="detail-item"><div className="detail-k">Account ID</div><div className="detail-v">{selected.ad_account_id.slice(0, 8)}</div></div>
                    </div>
                    {selectedProviderState ? (
                      <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #e3e7ee" }}>
                        <div className="kpi-title">Provider Readiness</div>
                        <div className="detail-grid" style={{ marginTop: 8 }}>
                          <div className="detail-item"><div className="detail-k">Provider</div><div className="detail-v">{providerLabel(selectedProviderState.provider)}</div></div>
                          <div className="detail-item"><div className="detail-k">Sync Ready</div><div className="detail-v">{selectedProviderState.sync_ready ? "Yes" : "No"}</div></div>
                          <div className="detail-item"><div className="detail-k">Auth State</div><div className="detail-v">{selectedProviderState.auth_state}</div></div>
                          <div className="detail-item"><div className="detail-k">Linked Users</div><div className="detail-v">{selectedProviderState.identity_linked_users}</div></div>
                        </div>
                        <div className="muted-note" style={{ marginTop: 8 }}>
                          Source: {selectedProviderState.connection_sources?.length ? selectedProviderState.connection_sources.join(", ") : "not configured"}
                        </div>
                        <div className="muted-note">
                          Missing: {selectedProviderState.missing_requirements?.length ? selectedProviderState.missing_requirements.join(", ") : "none"}
                        </div>
                      </div>
                    ) : null}
                    {selected.error_message ? (
                      <div className="alert-card high" style={{ marginTop: 10 }}>
                        <div className="alert-priority high">ERROR</div>
                        <div className="insight-text" style={{ color: "#9e2b2b", marginTop: 8 }}>{safeErrorMessage(selected.error_message)}</div>
                        <div className="muted-note" style={{ marginTop: 8 }}>
                          code: {selected.error_code || "n/a"} · category: {selected.error_category || "n/a"}
                        </div>
                        <div
                          style={{
                            marginTop: 8,
                            padding: 8,
                            border: "1px solid #f0c9c9",
                            borderRadius: 6,
                            background: "#fff7f7",
                            color: "#8b2525",
                            fontSize: 12,
                            lineHeight: 1.4,
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                          }}
                        >
                          {selected.error_message}
                        </div>
                      </div>
                    ) : null}
                  </div>

                  <div className="budgets-detail-actions">
                    <button className="primary-btn" onClick={() => void retrySelected()}>Retry Sync</button>
                    <button className="ghost-btn" onClick={() => void loadData()}>Refresh</button>
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
