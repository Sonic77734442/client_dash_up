"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import {
  ExternalAccountConfig,
  ExternalInsightsSummary,
  GoogleInsightsData,
  MetaInsightsData,
  TikTokInsightsData,
} from "../../lib/types";

function fmtNum(v?: number | string | null, digits = 0) {
  const n = Number(v || 0);
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(Number.isFinite(n) ? n : 0);
}

function fmtMoney(v?: number | string | null, currency = "USD") {
  const n = Number(v || 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);
}

function toPct(v?: number | string | null) {
  const n = Number(v || 0);
  return `${fmtNum(n * 100, 2)}%`;
}

function lastDays(days: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (days - 1));
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

function TableBlock({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  const columns = useMemo(() => {
    if (!rows.length) return [] as string[];
    const preferred = [
      "campaign_name",
      "adgroup_name",
      "ad_name",
      "campaign_id",
      "adgroup_id",
      "ad_id",
      "spend",
      "impressions",
      "clicks",
      "ctr",
      "cpc",
      "cpm",
      "conversions",
      "reach",
    ];
    const present = new Set<string>();
    for (const r of rows) Object.keys(r).forEach((k) => present.add(k));
    return preferred.filter((k) => present.has(k)).slice(0, 10);
  }, [rows]);

  return (
    <article className="panel" style={{ marginTop: 10 }}>
      <div className="panel-head">
        <h3>{title}</h3>
        <div className="panel-subtitle">{rows.length} rows</div>
      </div>
      <div className="budgets-table-wrap">
        <table className="budgets-table">
          <thead>
            <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.slice(0, 100).map((r, idx) => (
              <tr key={`${title}-${idx}`}>
                {columns.map((c) => {
                  const raw = r[c];
                  const isMoney = ["spend", "cpc", "cpm"].includes(c);
                  const isPct = c === "ctr";
                  const val =
                    isMoney ? fmtMoney(raw as number) : isPct ? toPct(raw as number) : typeof raw === "number" ? fmtNum(raw, 0) : String(raw ?? "--");
                  return <td key={`${idx}-${c}`}>{val}</td>;
                })}
              </tr>
            ))}
            {!rows.length ? (
              <tr>
                <td colSpan={Math.max(1, columns.length)}>No data</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </article>
  );
}

function Metrics({ summary }: { summary?: ExternalInsightsSummary }) {
  const currency = summary?.currency || "USD";
  return (
    <section className="kpi-grid" style={{ marginTop: 8 }}>
      <article className="kpi-card"><div className="kpi-title">Spend</div><div className="kpi-value">{fmtMoney(summary?.spend, currency)}</div></article>
      <article className="kpi-card"><div className="kpi-title">Impressions</div><div className="kpi-value">{fmtNum(summary?.impressions)}</div></article>
      <article className="kpi-card"><div className="kpi-title">Clicks</div><div className="kpi-value">{fmtNum(summary?.clicks)}</div></article>
      <article className="kpi-card"><div className="kpi-title">CTR</div><div className="kpi-value">{toPct(summary?.ctr)}</div></article>
      <article className="kpi-card"><div className="kpi-title">CPC</div><div className="kpi-value">{fmtMoney(summary?.cpc, currency)}</div></article>
      <article className="kpi-card"><div className="kpi-title">CPM</div><div className="kpi-value">{fmtMoney(summary?.cpm, currency)}</div></article>
    </section>
  );
}

export default function TrafficPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [dateFrom, setDateFrom] = useState(lastDays(30).from);
  const [dateTo, setDateTo] = useState(lastDays(30).to);

  const [accounts, setAccounts] = useState<ExternalAccountConfig[]>([]);
  const [metaAccount, setMetaAccount] = useState("");
  const [googleAccount, setGoogleAccount] = useState("");
  const [tiktokAccount, setTiktokAccount] = useState("");

  const [meta, setMeta] = useState<MetaInsightsData | null>(null);
  const [google, setGoogle] = useState<GoogleInsightsData | null>(null);
  const [tiktok, setTiktok] = useState<TikTokInsightsData | null>(null);

  const req = useCallback(
    <T,>(path: string) => fetchJson<T>(session.apiBase, path, session.token),
    [session.apiBase, session.token]
  );

  const metaAccounts = useMemo(() => accounts.filter((x) => x.platform === "meta"), [accounts]);
  const googleAccounts = useMemo(() => accounts.filter((x) => x.platform === "google"), [accounts]);
  const tiktokAccounts = useMemo(() => accounts.filter((x) => x.platform === "tiktok"), [accounts]);

  const loadAccounts = useCallback(async () => {
    const data = await req<{ items: ExternalAccountConfig[] }>("/accounts");
    setAccounts(data.items || []);
  }, [req]);

  const loadInsights = useCallback(async () => {
    const q = (id?: string) => {
      const p = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      if (id) p.set("account_id", id);
      return `?${p.toString()}`;
    };

    const [m, g, t] = await Promise.all([
      req<MetaInsightsData>(`/meta/insights${q(metaAccount || undefined)}`),
      req<GoogleInsightsData>(`/google/insights${q(googleAccount || undefined)}`),
      req<TikTokInsightsData>(`/tiktok/insights${q(tiktokAccount || undefined)}`),
    ]);

    setMeta(m);
    setGoogle(g);
    setTiktok(t);
  }, [dateFrom, dateTo, metaAccount, googleAccount, tiktokAccount, req]);

  useEffect(() => {
    if (!ready) return;
    void loadAccounts().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load accounts"));
  }, [ready, loadAccounts]);

  useEffect(() => {
    if (!ready) return;
    void loadInsights().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load traffic data"));
  }, [ready, loadInsights]);

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="traffic" subtitle="Traffic Drilldown" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="traffic" />
              <div className="topbar-title">Traffic by Platform</div>
              <div className="panel-subtitle">Separate Meta / Google / TikTok blocks with account-level pull and campaign drilldown.</div>
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
                    try {
                      await Promise.all([loadAccounts(), loadInsights()]);
                      push("Session saved", "success");
                    } catch (err) {
                      setWarning(err instanceof Error ? err.message : "Load failed");
                    }
                  }}
                  disabled={!ready}
                >
                  Save
                </button>
              </div>
            ) : null}
          </header>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head budgets-toolbar">
              <h3>Global Filters</h3>
              <div className="session-controls">
                <label>
                  From
                  <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
                </label>
                <label>
                  To
                  <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
                </label>
                <button className="primary-btn" onClick={() => void loadInsights()}>Apply</button>
              </div>
            </div>
          </section>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head budgets-toolbar">
              <h3>Meta Ads</h3>
              <div className="session-controls">
                <label>
                  Account
                  <select value={metaAccount} onChange={(e) => setMetaAccount(e.target.value)}>
                    <option value="">All Meta accounts</option>
                    {metaAccounts.map((a) => <option key={a.id} value={a.id}>{a.name || a.external_id}</option>)}
                  </select>
                </label>
                <button className="ghost-btn" onClick={() => void loadInsights()}>Reload</button>
              </div>
            </div>
            <div className="panel-subtitle">{meta?.status || "--"}</div>
            <Metrics summary={meta?.summary} />
            <TableBlock title="Campaigns" rows={meta?.campaigns || []} />
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head budgets-toolbar">
              <h3>Google Ads</h3>
              <div className="session-controls">
                <label>
                  Account
                  <select value={googleAccount} onChange={(e) => setGoogleAccount(e.target.value)}>
                    <option value="">All Google accounts</option>
                    {googleAccounts.map((a) => <option key={a.id} value={a.id}>{a.name || a.external_id}</option>)}
                  </select>
                </label>
                <button className="ghost-btn" onClick={() => void loadInsights()}>Reload</button>
              </div>
            </div>
            <div className="panel-subtitle">{google?.status || "--"}</div>
            <Metrics summary={google?.summary} />
            <TableBlock title="Campaigns" rows={google?.campaigns || []} />
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head budgets-toolbar">
              <h3>TikTok Ads</h3>
              <div className="session-controls">
                <label>
                  Account
                  <select value={tiktokAccount} onChange={(e) => setTiktokAccount(e.target.value)}>
                    <option value="">All TikTok accounts</option>
                    {tiktokAccounts.map((a) => <option key={a.id} value={a.id}>{a.name || a.external_id}</option>)}
                  </select>
                </label>
                <button className="ghost-btn" onClick={() => void loadInsights()}>Reload</button>
              </div>
            </div>
            <div className="panel-subtitle">{tiktok?.status || "--"}</div>
            <Metrics summary={tiktok?.summary} />
            <TableBlock title="Campaigns" rows={tiktok?.campaigns || []} />
            <TableBlock title="Ad Groups" rows={tiktok?.adgroups || []} />
            <TableBlock title="Ads" rows={tiktok?.ads || []} />
          </section>
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
