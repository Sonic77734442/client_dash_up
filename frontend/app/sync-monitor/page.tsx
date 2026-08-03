"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { DataSourcesNav } from "../../components/DataSourcesNav";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import {
  AdAccount,
  AdAccountDiscoverResponse,
  AdAccountSyncDiagnostic,
  AdAccountSyncDiagnosticsResponse,
  AdAccountSyncJob,
  AdAccountSyncRunResponse,
  AuthMeResponse,
  ClientOut,
  IntegrationConnection,
  IntegrationsOverview,
  IntegrationProvider,
} from "../../lib/types";

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString("ru-RU");
}

function statusClass(status: string) {
  return status === "success" ? "good" : "bad";
}

function syncStateClass(state: AdAccountSyncDiagnostic["sync_state"]) {
  if (state === "healthy") return "good";
  if (state === "retry_scheduled" || state === "never_synced") return "warn";
  return "bad";
}

function syncStateLabel(state: AdAccountSyncDiagnostic["sync_state"]) {
  if (state === "healthy") return "Работает";
  if (state === "retry_scheduled") return "Повтор запланирован";
  if (state === "never_synced") return "Ещё не обновлялся";
  return "Ошибка";
}

function authStateLabel(state?: string | null) {
  const s = String(state || "").trim().toLowerCase();
  if (!s) return "Нет данных";
  if (s === "connected" || s === "authorized" || s === "valid" || s === "configured") return "Подключена";
  if (s === "expired") return "Истекла";
  if (s === "revoked") return "Отозвана";
  if (s === "invalid") return "Недействительна";
  if (s === "missing") return "Не настроена";
  if (s === "disabled") return "Отключена";
  return state || "--";
}

function providerLabel(v: string) {
  const p = (v || "").toLowerCase();
  if (p === "meta" || p === "facebook") return "Meta Ads";
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

function connectionLabel(row: IntegrationConnection) {
  const label = String(row.connected_account_label || "").trim();
  if (label) return label;
  const preview = row.credentials_preview || {};
  const previewLabel = String(
    preview.email || preview.account_email || preview.login_email || preview.user_email || ""
  ).trim();
  if (previewLabel) return previewLabel;
  return row.scope_type;
}

function connectionScopeLabel(scope: IntegrationConnection["scope_type"]) {
  if (scope === "global") return "Вся платформа";
  if (scope === "agency") return "Агентство";
  return "Клиент";
}

function jobStatusLabel(status: string) {
  if (status === "success") return "Успешно";
  if (status === "error") return "Ошибка";
  if (status === "running") return "Выполняется";
  return status;
}

function actionHintLabel(raw?: string | null) {
  const value = String(raw || "").toLowerCase();
  if (!value) return "Откройте подключение и повторите обновление.";
  if (value.includes("reconnect") || value.includes("auth")) return "Переподключите рекламную платформу.";
  if (value.includes("permission") || value.includes("access")) return "Проверьте доступ пользователя к рекламному аккаунту.";
  if (value.includes("retry")) return "Повторите обновление данных.";
  if (value.includes("wait") || value.includes("later")) return "Подождите и повторите позже.";
  if (/[а-яё]/i.test(raw || "")) return raw || "";
  return "Откройте подключение, проверьте доступ и повторите обновление.";
}

function safeErrorMessage(raw?: string | null) {
  const msg = String(raw || "").toLowerCase();
  if (!msg) return "";
  if (msg.includes("expired") || msg.includes("unauthorized") || msg.includes("invalid token")) {
    return "Авторизация истекла или недействительна. Переподключите платформу.";
  }
  if (msg.includes("scope") || msg.includes("permission") || msg.includes("forbidden") || msg.includes("access")) {
    return "Недостаточно разрешений. Переподключите платформу и подтвердите доступ к рекламным аккаунтам.";
  }
  if (msg.includes("rate") || msg.includes("throttl") || msg.includes("quota")) {
    return "Платформа временно ограничила частоту запросов. Повторите позже.";
  }
  if (msg.includes("credential") || msg.includes("not set")) {
    return "Данные подключения отсутствуют или заполнены не полностью.";
  }
  if (msg.includes("customer") && msg.includes("not found")) {
    return "У подключённого Google-пользователя нет доступа к этому рекламному аккаунту.";
  }
  if (msg.includes("manager") && msg.includes("hierarchy")) {
    return "Аккаунт находится вне подключённого управляющего кабинета Google. Проверьте доступ MCC.";
  }
  if (msg.includes("developer token")) {
    return "Developer token Google отсутствует или недействителен.";
  }
  return "Не удалось обновить данные. Проверьте подсказку и повторите попытку.";
}

function requireItems<T>(payload: unknown, label: string): T[] {
  if (!payload || typeof payload !== "object") {
    throw new Error(`${label}: сервис вернул некорректный ответ`);
  }
  const items = (payload as { items?: unknown }).items;
  if (!Array.isArray(items)) {
    throw new Error(`${label}: сервис вернул некорректный список`);
  }
  return items as T[];
}

function defaultSyncRangeLastDays(days: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (days - 1));
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { date_from: fmt(from), date_to: fmt(to) };
}

function isoDate(d: Date) {
  return d.toISOString().slice(0, 10);
}

function monthBatches(fromIso: string, toIso: string) {
  const out: Array<{ date_from: string; date_to: string }> = [];
  const from = new Date(`${fromIso}T00:00:00.000Z`);
  const to = new Date(`${toIso}T00:00:00.000Z`);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from > to) return out;
  let cur = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate()));
  while (cur <= to) {
    const start = new Date(cur);
    const monthEnd = new Date(Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth() + 1, 0));
    const end = monthEnd < to ? monthEnd : to;
    out.push({ date_from: isoDate(start), date_to: isoDate(end) });
    cur = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate() + 1));
  }
  return out;
}

export default function SyncMonitorPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [jobs, setJobs] = useState<AdAccountSyncJob[]>([]);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationsOverview | null>(null);
  const [connections, setConnections] = useState<IntegrationConnection[]>([]);
  const [diagnostics, setDiagnostics] = useState<AdAccountSyncDiagnosticsResponse | null>(null);
  const [lastRun, setLastRun] = useState<AdAccountSyncRunResponse | null>(null);

  const [provider, setProvider] = useState("all");
  const [status, setStatus] = useState<"all" | "success" | "error">("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [syncLoading, setSyncLoading] = useState(false);
  const [discoverClientId, setDiscoverClientId] = useState("");
  const [currentRole, setCurrentRole] = useState<"admin" | "agency" | "client" | "unknown">("unknown");
  const [connectProviderName, setConnectProviderName] = useState<"google" | "facebook" | null>(null);
  const [connectMode, setConnectMode] = useState<"add" | "overwrite">("add");
  const [overwriteConnectionKey, setOverwriteConnectionKey] = useState("");
  const [historyProgress, setHistoryProgress] = useState("");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const diagParams = new URLSearchParams({ status: "active", limit: "500" });
    if (discoverClientId) diagParams.set("client_id", discoverClientId);
    const [jobRows, accRows, clientRows, integrationsRows, diagnosticsRows, connectionsRows] = await Promise.all([
      req<{ items: AdAccountSyncJob[] }>(`/ad-accounts/sync/jobs?status=all&limit=500`),
      req<{ items: AdAccount[] }>("/ad-accounts?status=all"),
      req<{ items: ClientOut[] }>("/clients?status=all"),
      req<IntegrationsOverview>("/integrations/overview"),
      req<AdAccountSyncDiagnosticsResponse>(`/ad-accounts/sync/diagnostics?${diagParams.toString()}`),
      req<{ items: IntegrationConnection[] }>("/me/integration-connections?status=all"),
    ]);
    const me = await req<AuthMeResponse>("/auth/me");
    if (
      !integrationsRows ||
      !integrationsRows.summary ||
      !Array.isArray(integrationsRows.providers) ||
      !Array.isArray(integrationsRows.events)
    ) {
      throw new Error("Источники рекламы: сервис вернул некорректные данные");
    }
    if (
      !diagnosticsRows ||
      !diagnosticsRows.summary ||
      !Array.isArray(diagnosticsRows.items)
    ) {
      throw new Error("Диагностика: сервис вернул некорректные данные");
    }
    setCurrentRole(me?.user?.role || "unknown");
    setJobs(requireItems<AdAccountSyncJob>(jobRows, "История синхронизации"));
    setAccounts(requireItems<AdAccount>(accRows, "Рекламные аккаунты"));
    setClients(requireItems<ClientOut>(clientRows, "Клиенты"));
    setIntegrations(integrationsRows);
    setDiagnostics(diagnosticsRows);
    setConnections(requireItems<IntegrationConnection>(connectionsRows, "Подключения"));
  }, [req, discoverClientId]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) =>
      setWarning(err instanceof Error ? err.message : "Не удалось загрузить состояние синхронизации")
    );
  }, [ready, loadData]);

  useEffect(() => {
    if (!discoverClientId && clients.length === 1) {
      setDiscoverClientId(clients[0].id);
    }
  }, [discoverClientId, clients]);

  const accountMap = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);
  const clientMap = useMemo(() => new Map(clients.map((c) => [c.id, c.name])), [clients]);
  const diagnosticsByAccount = useMemo(
    () => new Map((diagnostics?.items || []).map((d) => [d.ad_account_id, d])),
    [diagnostics]
  );

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

  const diagnosticRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (diagnostics?.items || [])
      .filter((d) => {
        if (provider === "all") return true;
        const selectedPlatform = asSyncPlatform(provider) || provider;
        const rowPlatform = asSyncPlatform(d.platform) || d.platform;
        return rowPlatform === selectedPlatform;
      })
      .filter((d) => {
        if (!q) return true;
        const clientName = d.client_name || clientMap.get(d.client_id) || "";
        const hay = `${d.platform} ${d.account_name} ${clientName} ${d.sync_state} ${d.diagnostic_message}`.toLowerCase();
        return hay.includes(q);
      })
      .sort((a, b) => new Date(b.last_sync_at || 0).getTime() - new Date(a.last_sync_at || 0).getTime());
  }, [diagnostics, provider, search, clientMap]);

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
  const selectedDiagnostic = useMemo(
    () => (selected?.ad_account_id ? diagnosticsByAccount.get(selected.ad_account_id) || null : null),
    [selected?.ad_account_id, diagnosticsByAccount]
  );
  const providerMap = useMemo(() => {
    const map = new Map<string, IntegrationProvider>();
    for (const p of integrations?.providers || []) {
      map.set((p.provider || "").toLowerCase(), p);
    }
    return map;
  }, [integrations]);
  const connectionsByProvider = useMemo(() => {
    const map = new Map<string, IntegrationConnection[]>();
    for (const row of connections) {
      const key = (asSyncPlatform(row.provider) || row.provider || "").toLowerCase();
      map.set(key, [...(map.get(key) || []), row]);
    }
    return map;
  }, [connections]);
  const selectedProviderState = useMemo(() => {
    if (!selected?.provider) return null;
    return providerMap.get((selected.provider || "").toLowerCase()) || null;
  }, [providerMap, selected?.provider]);

  const providerOptions = useMemo(() => ["all", ...Array.from(new Set(jobs.map((j) => j.provider))).sort()], [jobs]);

  function openConnectProvider(providerName: "google" | "facebook") {
    if (currentRole === "client") {
      push("Подключать платформы может агентство или администратор", "info");
      return;
    }
    setConnectProviderName(providerName);
    setConnectMode("add");
    setOverwriteConnectionKey("");
  }

  function openOverwriteConnection(row: IntegrationConnection) {
    const p = (row.provider || "").toLowerCase().trim();
    const providerName = p === "google" ? "google" : (p === "meta" || p === "facebook" ? "facebook" : null);
    if (!providerName) {
      push("Переподключение через интерфейс доступно для Google Ads и Meta Ads", "info");
      return;
    }
    setConnectProviderName(providerName);
    setConnectMode("overwrite");
    setOverwriteConnectionKey(row.connection_key || "");
  }

  function closeConnectDialog() {
    setConnectProviderName(null);
    setConnectMode("add");
    setOverwriteConnectionKey("");
  }

  function startConnectProvider() {
    if (!connectProviderName) return;
    const key = overwriteConnectionKey.trim();
    if (connectMode === "overwrite" && !key) {
      push("Не удалось определить существующее подключение. Обновите страницу и попробуйте снова.", "info");
      return;
    }
    const base = session.apiBase.trim().replace(/\/$/, "") || defaultApiBase;
    const q = new URLSearchParams({
      next: "/sync-monitor",
      intent: "connect",
      connect_mode: connectMode,
    });
    if (connectMode === "overwrite") {
      q.set("connection_key", key);
    }
    localStorage.setItem("ops_api_base", base);
    window.location.href = `${base}/auth/${connectProviderName}/start?${q.toString()}`;
  }

  async function runSync(opts?: { platform?: "meta" | "google" | "tiktok"; accountId?: string }) {
    if (currentRole === "client") {
      push("Обновлять данные может агентство или администратор", "info");
      return;
    }
    if (!opts?.accountId && !discoverClientId) {
      push("Выберите клиента перед обновлением данных", "info");
      return;
    }
    try {
      setSyncLoading(true);
      const payload: Record<string, unknown> = { ...defaultSyncRangeLastDays(30) };
      if (discoverClientId) payload.client_id = discoverClientId;
      if (opts?.platform) payload.platform = opts.platform;
      if (opts?.accountId) {
        payload.account_ids = [opts.accountId];
        payload.force = true;
      }
      const runRes = await req<AdAccountSyncRunResponse>("/ad-accounts/sync/run", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setLastRun(runRes);
      const scope = opts?.accountId ? "аккаунт" : opts?.platform ? providerLabel(opts.platform) : "все платформы";
      push(
        `Обновление завершено (${scope}): обработано ${runRes.processed}, успешно ${runRes.success}, с ошибкой ${runRes.failed}, пропущено ${runRes.skipped}`,
        runRes.failed > 0 ? "info" : "success"
      );
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось обновить данные";
      push(msg, "error");
    } finally {
      setSyncLoading(false);
    }
  }

  async function discoverAccounts(providerName?: "meta" | "google" | "tiktok") {
    if (currentRole === "client") {
      push("Искать рекламные аккаунты может агентство или администратор", "info");
      return;
    }
    try {
      setSyncLoading(true);
      const payload: Record<string, unknown> = { upsert_existing: true };
      if (discoverClientId) payload.client_id = discoverClientId;
      if (providerName) payload.provider = providerName;
      const res = await req<AdAccountDiscoverResponse>("/ad-accounts/discover", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const failCount = Object.keys(res.providers_failed || {}).length;
      const summary = `Найдено: новых ${res.created}, обновлено ${res.updated}, пропущено ${res.skipped}`;
      push(failCount ? `${summary}; ошибок платформ: ${failCount}` : summary, failCount ? "info" : "success");
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось найти рекламные аккаунты";
      push(msg, "error");
    } finally {
      setSyncLoading(false);
    }
  }

  async function runFullHistorySync() {
    if (currentRole === "client") {
      push("Загружать историю может агентство или администратор", "info");
      return;
    }
    if (!discoverClientId) {
      push("Выберите клиента перед загрузкой истории", "info");
      return;
    }
    const raw = window.prompt("С какой даты загрузить историю? Формат: ГГГГ-ММ-ДД", "2026-01-01");
    const from = String(raw || "").trim();
    if (!from) return;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(from)) {
      push("Неверный формат даты. Используйте ГГГГ-ММ-ДД", "error");
      return;
    }
    const to = isoDate(new Date());
    const batches = monthBatches(from, to);
    if (!batches.length) {
      push("Проверьте диапазон дат", "error");
      return;
    }
    try {
      setSyncLoading(true);
      let totalProcessed = 0;
      let totalSuccess = 0;
      let totalFailed = 0;
      let totalSkipped = 0;
      for (let i = 0; i < batches.length; i += 1) {
        const b = batches[i];
        setHistoryProgress(`Загрузка истории ${i + 1} из ${batches.length}: ${b.date_from} — ${b.date_to}`);
        const payload: Record<string, unknown> = {
          force: true,
          client_id: discoverClientId,
          date_from: b.date_from,
          date_to: b.date_to,
        };
        const runRes = await req<AdAccountSyncRunResponse>("/ad-accounts/sync/run", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        totalProcessed += runRes.processed || 0;
        totalSuccess += runRes.success || 0;
        totalFailed += runRes.failed || 0;
        totalSkipped += runRes.skipped || 0;
      }
      push(
        `История загружена: обработано ${totalProcessed}, успешно ${totalSuccess}, с ошибкой ${totalFailed}, пропущено ${totalSkipped}`,
        totalFailed > 0 ? "info" : "success"
      );
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось загрузить историю";
      push(msg, "error");
    } finally {
      setHistoryProgress("");
      setSyncLoading(false);
    }
  }

  async function retrySelected() {
    if (!selected?.ad_account_id) {
      push("Сначала выберите запуск в истории", "info");
      return;
    }
    await runSync({ accountId: selected.ad_account_id });
  }

  async function disconnectConnection(row: IntegrationConnection) {
    if (currentRole === "client") {
      push("Отключать платформы может агентство или администратор", "info");
      return;
    }
    if (!window.confirm(`Отключить ${providerLabel(row.provider)} — ${connectionLabel(row)}?`)) {
      return;
    }
    try {
      await req(`/me/integration-connections/${row.id}`, { method: "DELETE" });
      push("Подключение отключено", "success");
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось отключить платформу";
      push(msg, "error");
    }
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
        <AppSidebar active="sync_monitor" />

        <main className="content">
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs active="sync_monitor" sectionLabel="Источники рекламы" />
              <div className="topbar-title">Синхронизация и диагностика</div>
              <div className="panel-subtitle">
                Подключайте платформы, загружайте аккаунты и исправляйте ошибки по понятным подсказкам
              </div>
            </div>
            {tokenLoginEnabled ? (
              <details className="debug-session">
                <summary>Тестовая сессия</summary>
                <div className="debug-session-popover">
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
                      const next = {
                        apiBase: session.apiBase.trim().replace(/\/$/, "") || defaultApiBase,
                        token: session.token.trim(),
                      };
                      persist(next);
                      setSession(next);
                      await loadData();
                      push("Сессия сохранена", "success");
                    }}
                    disabled={!ready}
                  >
                    Сохранить
                  </button>
                </div>
              </details>
            ) : null}
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <DataSourcesNav active="sync" />

          <section className="kpi-grid" style={{ marginTop: 12 }}>
            <article className="kpi-card"><div className="kpi-title">Запусков</div><div className="kpi-value">{kpis.total}</div></article>
            <article className="kpi-card good"><div className="kpi-title">Успешно</div><div className="kpi-value">{kpis.success}</div></article>
            <article className="kpi-card bad"><div className="kpi-title">С ошибкой</div><div className="kpi-value">{kpis.error}</div></article>
            <article className="kpi-card"><div className="kpi-title">Аккаунтов обновлялось</div><div className="kpi-value">{kpis.uniqueAccounts}</div></article>
          </section>

          <section className="panel" id="provider-connections" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>1. Подключите рекламную платформу</h3>
                <div className="panel-subtitle">
                  Предоставьте доступ к рекламным кабинетам Google Ads или Meta Ads. Это не вход в платформу.
                </div>
              </div>
              <div className="data-connection-actions">
                <label>
                  <span>Клиент для найденных аккаунтов</span>
                <select
                  value={discoverClientId}
                  onChange={(e) => setDiscoverClientId(e.target.value)}
                  title="Можно оставить аккаунты без клиента и распределить позже"
                >
                  <option value="">Назначить позже</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                </label>
                <button className="ghost-btn" onClick={() => void discoverAccounts()} disabled={syncLoading}>
                  Найти аккаунты
                </button>
                <button className="primary-btn" onClick={() => openConnectProvider("google")}>Подключить Google Ads</button>
                <button className="primary-btn" onClick={() => openConnectProvider("facebook")}>Подключить Meta Ads</button>
              </div>
            </div>
            {currentRole === "client" ? (
              <div className="muted-note" style={{ marginTop: 8 }}>
                У клиента здесь режим просмотра. Подключениями и обновлением управляет агентство.
              </div>
            ) : null}
            {historyProgress ? (
              <div className="muted-note" style={{ marginTop: 8 }}>{historyProgress}</div>
            ) : null}
            <div className="kpi-grid" style={{ marginTop: 10 }}>
              {(Array.isArray(integrations?.providers) ? integrations.providers : []).map((p) => (
                <article key={p.provider} className={`kpi-card ${providerStatusClass(p.status)}`}>
                  <div className="kpi-title">{providerLabel(p.provider)}</div>
                  <div className="kpi-value" style={{ fontSize: 22 }}>{p.sync_ready ? "Готово" : "Нужна настройка"}</div>
                  <div className="muted-note">
                    Авторизация: {(connectionsByProvider.get(asSyncPlatform(p.provider) || (p.provider || "").toLowerCase()) || [])
                      .filter((row) => row.status === "active")
                      .map(connectionLabel)
                      .join(", ") || "не подключена"}
                  </div>
                  <div className="muted-note">
                    Аккаунтов: {p.linked_accounts_count}
                  </div>
                  <div style={{ marginTop: 8 }}>
                    {!p.sync_ready ? (
                      asSyncPlatform(p.provider) === "google" ? (
                        <button className="primary-btn" onClick={() => openConnectProvider("google")}>
                          Подключить Google Ads
                        </button>
                      ) : asSyncPlatform(p.provider) === "meta" ? (
                        <button className="primary-btn" onClick={() => openConnectProvider("facebook")}>
                          Подключить Meta Ads
                        </button>
                      ) : (
                        <button
                          className="ghost-btn"
                          disabled
                          title="Подключение TikTok настраивается администратором платформы"
                        >
                          Нужна настройка администратора
                        </button>
                      )
                    ) : (
                      <>
                        <button
                          className="ghost-btn"
                          onClick={() => {
                            const platform = asSyncPlatform(p.provider);
                            if (!platform) {
                              push("Эта платформа пока не поддерживает обновление", "info");
                              return;
                            }
                            void runSync({ platform });
                          }}
                          disabled={syncLoading || !discoverClientId}
                          title={!discoverClientId ? "Сначала выберите клиента" : undefined}
                        >
                          Обновить {providerLabel(p.provider)}
                        </button>
                        <button
                          className="ghost-btn"
                          onClick={() => {
                            const platform = asSyncPlatform(p.provider);
                            if (!platform) {
                              push("Для этой платформы пока нельзя искать аккаунты", "info");
                              return;
                            }
                            void discoverAccounts(platform);
                          }}
                          disabled={syncLoading}
                        >
                          Найти аккаунты
                        </button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
            <div className="data-sync-primary-actions">
              <button
                className="primary-btn"
                onClick={() => void runSync()}
                disabled={syncLoading || !discoverClientId}
                title={!discoverClientId ? "Сначала выберите клиента" : undefined}
              >
                {syncLoading ? "Обновляем…" : "Обновить данные за 30 дней"}
              </button>
              <button
                className="ghost-btn"
                onClick={() => void runFullHistorySync()}
                disabled={syncLoading || !discoverClientId}
                title={!discoverClientId ? "Сначала выберите клиента" : undefined}
              >
                Загрузить историю
              </button>
            </div>
          </section>

          <section className="panel" id="sync-diagnostics" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>2. Проверьте качество данных</h3>
                <div className="panel-subtitle">Для каждой ошибки показана причина и конкретное следующее действие.</div>
              </div>
              <button className="ghost-btn" onClick={() => void loadData()}>Обновить состояние</button>
            </div>
            <div className="kpi-grid" style={{ marginTop: 10 }}>
              <article className="kpi-card">
                <div className="kpi-title">Всего аккаунтов</div>
                <div className="kpi-value">{diagnostics?.summary?.total_accounts || 0}</div>
              </article>
              <article className="kpi-card good">
                <div className="kpi-title">Работают</div>
                <div className="kpi-value">{diagnostics?.summary?.healthy || 0}</div>
              </article>
              <article className="kpi-card bad">
                <div className="kpi-title">Ошибки</div>
                <div className="kpi-value">{diagnostics?.summary?.error || 0}</div>
              </article>
              <article className="kpi-card warn">
                <div className="kpi-title">Повтор запланирован</div>
                <div className="kpi-value">{diagnostics?.summary?.retry_scheduled || 0}</div>
              </article>
            </div>
            {lastRun ? (
              <div className="muted-note" style={{ marginTop: 10 }}>
                Последний запуск: обработано {lastRun.processed}, успешно {lastRun.success}, с ошибкой {lastRun.failed},
                пропущено {lastRun.skipped}, повтор запланирован для {lastRun.retry_scheduled}.
              </div>
            ) : null}
            <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
              <table className="budgets-table">
                <thead>
                  <tr>
                    <th>Платформа</th>
                    <th>Аккаунт</th>
                    <th>Клиент</th>
                    <th>Состояние</th>
                    <th>Причина</th>
                    <th>Что сделать</th>
                    <th>Обновлён</th>
                  </tr>
                </thead>
                <tbody>
                  {diagnosticRows.slice(0, 100).map((d) => (
                    <tr key={d.ad_account_id}>
                      <td>{providerLabel(d.platform)}</td>
                      <td>{d.account_name}</td>
                      <td>{d.client_name || clientMap.get(d.client_id) || "--"}</td>
                      <td><span className={`badge ${syncStateClass(d.sync_state)}`}>{syncStateLabel(d.sync_state)}</span></td>
                      <td>{d.sync_state === "healthy" ? "Данные обновляются без ошибок." : safeErrorMessage(d.diagnostic_message)}</td>
                      <td>{d.sync_state === "healthy" ? "Действий не требуется." : actionHintLabel(d.action_hint)}</td>
                      <td>{fmtDate(d.last_sync_at)}</td>
                    </tr>
                  ))}
                  {!diagnosticRows.length ? (
                    <tr>
                      <td colSpan={7} className="muted-note">Диагностических данных пока нет.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>3. Управляйте авторизациями</h3>
                <div className="panel-subtitle">
                  Здесь видны подключённые учётные записи. Секреты и токены никогда не показываются.
                </div>
              </div>
              <button className="ghost-btn" onClick={() => void loadData()}>Обновить список</button>
            </div>
            <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
              <table className="budgets-table">
                <thead>
                  <tr>
                    <th>Платформа</th>
                    <th>Учётная запись</th>
                    <th>Уровень доступа</th>
                    <th>Состояние</th>
                    <th>Обновлено</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {connections.map((row) => (
                    <tr key={row.id}>
                      <td>{providerLabel(row.provider)}</td>
                      <td>{connectionLabel(row)}</td>
                      <td>{connectionScopeLabel(row.scope_type)}</td>
                      <td>
                        <span className={`badge ${row.status === "active" ? "good" : "warn"}`}>
                          {row.status === "active" ? "Активно" : "В архиве"}
                        </span>
                      </td>
                      <td>{fmtDate(row.updated_at)}</td>
                      <td>
                        <button className="ghost-btn" onClick={() => openOverwriteConnection(row)} disabled={syncLoading}>
                          Переподключить
                        </button>
                        <button className="ghost-btn" onClick={() => void disconnectConnection(row)} disabled={syncLoading || row.status !== "active"}>
                          Отключить
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!connections.length ? (
                    <tr>
                      <td colSpan={6} className="muted-note">Подключённых учётных записей пока нет.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <section className="accounts-grid" style={{ marginTop: 12 }}>
            <article className="panel accounts-main">
              <div className="panel-head" style={{ marginBottom: 10 }}>
                <div>
                  <h3 style={{ margin: 0 }}>История обновлений</h3>
                  <div className="panel-subtitle">Последние запуски по платформам и рекламным аккаунтам</div>
                </div>
              </div>
              <div className="chip-row" style={{ marginTop: 0 }}>
                <label>
                  Платформа
                  <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                    {providerOptions.map((p) => (
                      <option key={p} value={p}>{p === "all" ? "Все" : providerLabel(p)}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Состояние
                  <select value={status} onChange={(e) => setStatus(e.target.value as "all" | "success" | "error")}>
                    <option value="all">Все</option>
                    <option value="success">Успешно</option>
                    <option value="error">С ошибкой</option>
                  </select>
                </label>
                <input className="clientops-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Найти платформу, аккаунт или клиента" />
                <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
              </div>

              <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
                <table className="budgets-table">
                  <thead>
                    <tr>
                      <th>Платформа</th>
                      <th>Состояние</th>
                      <th>Аккаунт</th>
                      <th>Клиент</th>
                      <th>Запущено</th>
                      <th>Записей</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const acc = accountMap.get(r.ad_account_id);
                      const clientName = acc ? clientMap.get(acc.client_id) || "--" : "--";
                      return (
                        <tr key={r.id} className={selectedId === r.id ? "selected" : ""} onClick={() => setSelectedId(r.id)}>
                          <td>{providerLabel(r.provider)}</td>
                          <td><span className={`badge ${statusClass(r.status)}`}>{jobStatusLabel(r.status)}</span></td>
                          <td>{acc?.name || r.ad_account_id.slice(0, 8)}</td>
                          <td>{clientName}</td>
                          <td>{fmtDate(r.started_at)}</td>
                          <td>{r.records_synced}</td>
                        </tr>
                      );
                    })}
                    {!rows.length ? (
                      <tr>
                        <td colSpan={6} className="muted-note">Запусков по выбранным фильтрам нет.</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </article>

            <aside className="panel accounts-detail">
              {!selected ? (
                <div className="data-empty-state compact">
                  <strong>Выберите запуск</strong>
                  <span>Здесь появятся результат, причина ошибки и следующее действие.</span>
                </div>
              ) : (
                <>
                  <div className="budgets-detail-head">
                    <div>
                      <div className="kpi-title">Результат запуска</div>
                      <h3 style={{ margin: 0 }}>{providerLabel(selected.provider)} · {jobStatusLabel(selected.status)}</h3>
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="detail-grid">
                      <div className="detail-item"><div className="detail-k">Запущено</div><div className="detail-v">{fmtDate(selected.started_at)}</div></div>
                      <div className="detail-item"><div className="detail-k">Завершено</div><div className="detail-v">{fmtDate(selected.finished_at)}</div></div>
                      <div className="detail-item"><div className="detail-k">Записей загружено</div><div className="detail-v">{selected.records_synced}</div></div>
                      <div className="detail-item"><div className="detail-k">ID аккаунта</div><div className="detail-v">{selected.ad_account_id.slice(0, 8)}</div></div>
                    </div>
                    {selectedProviderState ? (
                      <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #e3e7ee" }}>
                        <div className="kpi-title">Готовность платформы</div>
                        <div className="detail-grid" style={{ marginTop: 8 }}>
                          <div className="detail-item"><div className="detail-k">Платформа</div><div className="detail-v">{providerLabel(selectedProviderState.provider)}</div></div>
                          <div className="detail-item"><div className="detail-k">Готова к обновлению</div><div className="detail-v">{selectedProviderState.sync_ready ? "Да" : "Нет"}</div></div>
                          <div className="detail-item"><div className="detail-k">Авторизация</div><div className="detail-v">{authStateLabel(selectedProviderState.auth_state)}</div></div>
                          <div className="detail-item"><div className="detail-k">Пользователей подключено</div><div className="detail-v">{selectedProviderState.identity_linked_users}</div></div>
                        </div>
                        <div className="muted-note" style={{ marginTop: 8 }}>
                          Учётная запись: {(connectionsByProvider.get(asSyncPlatform(selectedProviderState.provider) || (selectedProviderState.provider || "").toLowerCase()) || [])
                            .filter((row) => row.status === "active")
                            .map(connectionLabel)
                            .join(", ") || "не подключена"}
                        </div>
                      </div>
                    ) : null}
                    {selectedDiagnostic && selectedDiagnostic.sync_state !== "healthy" ? (
                      <div className="alert-card high" style={{ marginTop: 10 }}>
                        <div className="alert-priority high">{syncStateLabel(selectedDiagnostic.sync_state).toUpperCase()}</div>
                        <div className="insight-text" style={{ color: "#9e2b2b", marginTop: 8 }}>{safeErrorMessage(selectedDiagnostic.diagnostic_message)}</div>
                        <div className="muted-note" style={{ marginTop: 8 }}>{actionHintLabel(selectedDiagnostic.action_hint)}</div>
                      </div>
                    ) : selected.error_message ? (
                      <div className="alert-card high" style={{ marginTop: 10 }}>
                        <div className="alert-priority high">ОШИБКА</div>
                        <div className="insight-text" style={{ color: "#9e2b2b", marginTop: 8 }}>{safeErrorMessage(selected.error_message)}</div>
                      </div>
                    ) : null}
                  </div>

                  <div className="budgets-detail-actions">
                    <button className="primary-btn" onClick={() => void retrySelected()}>Повторить обновление</button>
                    <button className="ghost-btn" onClick={() => void loadData()}>Обновить состояние</button>
                  </div>
                </>
              )}
            </aside>
          </section>
        </main>
      </div>

      {connectProviderName ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(9, 16, 30, 0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1200,
            padding: 16,
          }}
        >
          <div className="panel" style={{ width: "min(560px, 96vw)" }}>
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>
                  {connectMode === "overwrite" ? "Переподключить" : "Подключить"} {providerLabel(connectProviderName)}
                </h3>
                <div className="panel-subtitle">
                  Вы перейдёте на безопасную страницу платформы и подтвердите доступ к рекламным аккаунтам.
                </div>
              </div>
            </div>
            {connectMode === "overwrite" ? (
              <div className="data-next-step" style={{ marginTop: 12 }}>
                <strong>Что произойдёт</strong>
                <span>Текущая авторизация будет обновлена, а привязанные клиенты и аккаунты сохранятся.</span>
              </div>
            ) : (
              <div className="data-next-step success" style={{ marginTop: 12 }}>
                <strong>Новое подключение</strong>
                <span>Можно подключить ещё одного пользователя или управляющий кабинет — существующие подключения сохранятся.</span>
              </div>
            )}
            <div className="budgets-detail-actions" style={{ marginTop: 14 }}>
              <button className="primary-btn" onClick={startConnectProvider}>Перейти к авторизации</button>
              <button className="ghost-btn" onClick={closeConnectDialog}>Отмена</button>
            </div>
          </div>
        </div>
      ) : null}

      <ToastHost toasts={toasts} />
    </>
  );
}
