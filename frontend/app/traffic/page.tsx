"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import { AdAccount, AdStat } from "../../lib/types";

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

type TrafficSummary = {
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  ctr: number;
  cpc: number;
  cpm: number;
  currency: string;
};

function summarize(rows: AdStat[]): TrafficSummary {
  const spend = rows.reduce((sum, row) => sum + Number(row.spend || 0), 0);
  const impressions = rows.reduce((sum, row) => sum + Number(row.impressions || 0), 0);
  const clicks = rows.reduce((sum, row) => sum + Number(row.clicks || 0), 0);
  const conversions = rows.reduce((sum, row) => sum + Number(row.conversions || 0), 0);
  return {
    spend,
    impressions,
    clicks,
    conversions,
    ctr: impressions ? clicks / impressions : 0,
    cpc: clicks ? spend / clicks : 0,
    cpm: impressions ? (spend / impressions) * 1000 : 0,
    currency: "USD",
  };
}

function groupByAccount(rows: AdStat[], accountMap: Map<string, AdAccount>) {
  const buckets = new Map<string, { spend: number; impressions: number; clicks: number; conversions: number }>();
  for (const row of rows) {
    const accountId = String(row.ad_account_id || "").trim();
    if (!accountId) continue;
    const bucket = buckets.get(accountId) || { spend: 0, impressions: 0, clicks: 0, conversions: 0 };
    bucket.spend += Number(row.spend || 0);
    bucket.impressions += Number(row.impressions || 0);
    bucket.clicks += Number(row.clicks || 0);
    bucket.conversions += Number(row.conversions || 0);
    buckets.set(accountId, bucket);
  }
  return [...buckets.entries()]
    .map(([accountId, v]) => ({
      account_id: accountId,
      account_name: accountMap.get(accountId)?.name || accountId,
      spend: v.spend,
      impressions: v.impressions,
      clicks: v.clicks,
      conversions: v.conversions,
      ctr: v.impressions ? v.clicks / v.impressions : 0,
      cpc: v.clicks ? v.spend / v.clicks : 0,
      cpm: v.impressions ? (v.spend / v.impressions) * 1000 : 0,
    }))
    .sort((a, b) => b.spend - a.spend);
}

function groupDaily(rows: AdStat[]) {
  const buckets = new Map<string, { spend: number; impressions: number; clicks: number; conversions: number }>();
  for (const row of rows) {
    const d = String(row.date || "").trim();
    if (!d) continue;
    const bucket = buckets.get(d) || { spend: 0, impressions: 0, clicks: 0, conversions: 0 };
    bucket.spend += Number(row.spend || 0);
    bucket.impressions += Number(row.impressions || 0);
    bucket.clicks += Number(row.clicks || 0);
    bucket.conversions += Number(row.conversions || 0);
    buckets.set(d, bucket);
  }
  return [...buckets.entries()]
    .map(([date, v]) => ({
      date,
      spend: v.spend,
      impressions: v.impressions,
      clicks: v.clicks,
      conversions: v.conversions,
      ctr: v.impressions ? v.clicks / v.impressions : 0,
      cpc: v.clicks ? v.spend / v.clicks : 0,
      cpm: v.impressions ? (v.spend / v.impressions) * 1000 : 0,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

function TableBlock({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  const columns = useMemo(() => {
    if (!rows.length) return [] as string[];
    const preferred = [
      "date",
      "account_name",
      "account_id",
      "spend",
      "impressions",
      "clicks",
      "conversions",
      "ctr",
      "cpc",
      "cpm",
    ];
    const present = new Set<string>();
    for (const r of rows) Object.keys(r).forEach((k) => present.add(k));
    return preferred.filter((k) => present.has(k));
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
            {rows.slice(0, 200).map((r, idx) => (
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

function Metrics({ summary }: { summary: TrafficSummary }) {
  const currency = summary.currency || "USD";
  return (
    <section className="kpi-grid" style={{ marginTop: 8 }}>
      <article className="kpi-card"><div className="kpi-title">Spend</div><div className="kpi-value">{fmtMoney(summary.spend, currency)}</div></article>
      <article className="kpi-card"><div className="kpi-title">Impressions</div><div className="kpi-value">{fmtNum(summary.impressions)}</div></article>
      <article className="kpi-card"><div className="kpi-title">Clicks</div><div className="kpi-value">{fmtNum(summary.clicks)}</div></article>
      <article className="kpi-card"><div className="kpi-title">Conversions</div><div className="kpi-value">{fmtNum(summary.conversions, 2)}</div></article>
      <article className="kpi-card"><div className="kpi-title">CTR</div><div className="kpi-value">{toPct(summary.ctr)}</div></article>
      <article className="kpi-card"><div className="kpi-title">CPC</div><div className="kpi-value">{fmtMoney(summary.cpc, currency)}</div></article>
      <article className="kpi-card"><div className="kpi-title">CPM</div><div className="kpi-value">{fmtMoney(summary.cpm, currency)}</div></article>
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
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [metaRows, setMetaRows] = useState<AdStat[]>([]);
  const [googleRows, setGoogleRows] = useState<AdStat[]>([]);
  const [tiktokRows, setTiktokRows] = useState<AdStat[]>([]);
  const [metaAccount, setMetaAccount] = useState("");
  const [googleAccount, setGoogleAccount] = useState("");
  const [tiktokAccount, setTiktokAccount] = useState("");

  const req = useCallback(
    <T,>(path: string) => fetchJson<T>(session.apiBase, path, session.token),
    [session.apiBase, session.token]
  );

  const metaAccounts = useMemo(() => accounts.filter((x) => x.platform === "meta"), [accounts]);
  const googleAccounts = useMemo(() => accounts.filter((x) => x.platform === "google"), [accounts]);
  const tiktokAccounts = useMemo(() => accounts.filter((x) => x.platform === "tiktok"), [accounts]);
  const accountMap = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);

  const loadAccounts = useCallback(async () => {
    const data = await req<{ items: AdAccount[] }>("/ad-accounts?status=active");
    setAccounts(data.items || []);
  }, [req]);

  const loadStats = useCallback(async () => {
    const q = (platform: "meta" | "google" | "tiktok", accountId?: string) => {
      const p = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, platform });
      if (accountId) p.set("account_id", accountId);
      return `/ad-stats?${p.toString()}`;
    };
    const [m, g, t] = await Promise.all([
      req<{ items: AdStat[] }>(q("meta", metaAccount || undefined)),
      req<{ items: AdStat[] }>(q("google", googleAccount || undefined)),
      req<{ items: AdStat[] }>(q("tiktok", tiktokAccount || undefined)),
    ]);
    setMetaRows(m.items || []);
    setGoogleRows(g.items || []);
    setTiktokRows(t.items || []);
  }, [dateFrom, dateTo, metaAccount, googleAccount, tiktokAccount, req]);

  useEffect(() => {
    if (!ready) return;
    void loadAccounts().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load accounts"));
  }, [ready, loadAccounts]);

  useEffect(() => {
    if (!ready) return;
    void loadStats().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load traffic data"));
  }, [ready, loadStats]);

  const metaSummary = useMemo(() => summarize(metaRows), [metaRows]);
  const googleSummary = useMemo(() => summarize(googleRows), [googleRows]);
  const tiktokSummary = useMemo(() => summarize(tiktokRows), [tiktokRows]);

  const metaByAccount = useMemo(() => groupByAccount(metaRows, accountMap), [metaRows, accountMap]);
  const googleByAccount = useMemo(() => groupByAccount(googleRows, accountMap), [googleRows, accountMap]);
  const tiktokByAccount = useMemo(() => groupByAccount(tiktokRows, accountMap), [tiktokRows, accountMap]);

  const metaDaily = useMemo(() => groupDaily(metaRows), [metaRows]);
  const googleDaily = useMemo(() => groupDaily(googleRows), [googleRows]);
  const tiktokDaily = useMemo(() => groupDaily(tiktokRows), [tiktokRows]);

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="traffic" subtitle="Traffic Drilldown" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="traffic" />
              <div className="topbar-title">Traffic by Platform</div>
              <div className="panel-subtitle">Unified stats from synced ad accounts.</div>
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
                      await Promise.all([loadAccounts(), loadStats()]);
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
                <button className="primary-btn" onClick={() => void loadStats()}>Apply</button>
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
                    {metaAccounts.map((a) => <option key={a.id} value={a.id}>{a.name || a.external_account_id}</option>)}
                  </select>
                </label>
                <button className="ghost-btn" onClick={() => void loadStats()}>Reload</button>
              </div>
            </div>
            <div className="panel-subtitle">{metaRows.length} synced rows</div>
            <Metrics summary={metaSummary} />
            <TableBlock title="By Account" rows={metaByAccount} />
            <TableBlock title="Daily" rows={metaDaily} />
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head budgets-toolbar">
              <h3>Google Ads</h3>
              <div className="session-controls">
                <label>
                  Account
                  <select value={googleAccount} onChange={(e) => setGoogleAccount(e.target.value)}>
                    <option value="">All Google accounts</option>
                    {googleAccounts.map((a) => <option key={a.id} value={a.id}>{a.name || a.external_account_id}</option>)}
                  </select>
                </label>
                <button className="ghost-btn" onClick={() => void loadStats()}>Reload</button>
              </div>
            </div>
            <div className="panel-subtitle">{googleRows.length} synced rows</div>
            <Metrics summary={googleSummary} />
            <TableBlock title="By Account" rows={googleByAccount} />
            <TableBlock title="Daily" rows={googleDaily} />
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head budgets-toolbar">
              <h3>TikTok Ads</h3>
              <div className="session-controls">
                <label>
                  Account
                  <select value={tiktokAccount} onChange={(e) => setTiktokAccount(e.target.value)}>
                    <option value="">All TikTok accounts</option>
                    {tiktokAccounts.map((a) => <option key={a.id} value={a.id}>{a.name || a.external_account_id}</option>)}
                  </select>
                </label>
                <button className="ghost-btn" onClick={() => void loadStats()}>Reload</button>
              </div>
            </div>
            <div className="panel-subtitle">{tiktokRows.length} synced rows</div>
            <Metrics summary={tiktokSummary} />
            <TableBlock title="By Account" rows={tiktokByAccount} />
            <TableBlock title="Daily" rows={tiktokDaily} />
          </section>
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
