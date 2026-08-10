"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { DataSourcesNav } from "../../components/DataSourcesNav";
import { ToastHost } from "../../components/ToastHost";
import { agencySelectionRequiredMessage, useAgencyContext } from "../../hooks/useAgencyContext";
import { useSession } from "../../hooks/useSession";
import { useScopeRequestGuard } from "../../hooks/useScopeRequestGuard";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import { dataFreshnessMeta, providerDataFreshness, syncRunFeedback } from "../../lib/dataFreshness";
import { AdAccount, AdAccountSyncRunResponse, IntegrationConnection, IntegrationsOverview, IntegrationProvider } from "../../lib/types";

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString("ru-RU");
}

function providerLabel(v: string) {
  const p = (v || "").toLowerCase();
  if (p === "meta") return "Meta";
  if (p === "google") return "Google Ads";
  if (p === "tiktok") return "TikTok";
  return v;
}

function statusClass(provider: IntegrationProvider) {
  return dataFreshnessMeta(providerDataFreshness(provider)).tone;
}

function statusLabel(provider: IntegrationProvider) {
  if ((provider.assignment_conflict_accounts_count || 0) > 0) return "Конфликт привязки";
  if (!provider.sync_ready || provider.status === "disconnected") return "Нужна настройка";
  return dataFreshnessMeta(providerDataFreshness(provider)).label;
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

function providerMark(provider: string) {
  const value = providerLabel(provider);
  if (value === "Google Ads") return "G";
  if (value === "Meta") return "M";
  if (value === "TikTok") return "T";
  return value.slice(0, 1).toUpperCase();
}

function accountCountLabel(count: number) {
  const mod100 = Math.abs(count) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} аккаунтов`;
  if (mod10 === 1) return `${count} аккаунт`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} аккаунта`;
  return `${count} аккаунтов`;
}

function readableEventTitle(title: string) {
  const value = String(title || "").toLowerCase();
  if (value.includes("sync completed")) return "Данные обновлены";
  if (value.includes("sync failed")) return "Ошибка обновления";
  if (value.includes("connected")) return "Платформа подключена";
  return title || "Событие подключения";
}

function readableEventMessage(message: string) {
  const value = String(message || "").toLowerCase();
  if (value.includes("completed successfully")) return "Синхронизация завершена успешно.";
  if (value.includes("insufficient permissions")) {
    return "Недостаточно разрешений. Переподключите платформу и подтвердите доступ к рекламным аккаунтам.";
  }
  if (value.includes("expired") || value.includes("invalid token")) {
    return "Авторизация истекла. Переподключите платформу.";
  }
  if (value.includes("request failed") || value.includes("provider request")) {
    return "Не удалось получить данные от рекламной платформы. Откройте диагностику и повторите обновление.";
  }
  return message || "Дополнительных сведений нет.";
}

export default function IntegrationsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const agencyContext = useAgencyContext({ apiBase: session.apiBase, token: session.token, loadPortfolio: true });
  const soloClient = agencyContext.role === "solo_client";
  const beginScopedRequest = useScopeRequestGuard(
    agencyContext.selectedAgencyId || agencyContext.managedClientId || agencyContext.role || "unknown",
  );
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [data, setData] = useState<IntegrationsOverview | null>(null);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [search, setSearch] = useState("");
  const [selectedProvider, setSelectedProvider] = useState("");
  const [syncLoading, setSyncLoading] = useState(false);
  const [loading, setLoading] = useState(true);

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const isCurrentRequest = beginScopedRequest();
    setLoading(true);
    try {
      if (agencyContext.role === "agency" && !agencyContext.portfolioReady) {
        throw new Error(
          agencyContext.selectionRequired
            ? agencySelectionRequiredMessage()
            : agencyContext.portfolioError || "Не удалось загрузить портфель агентства.",
        );
      }
      if (soloClient && !agencyContext.soloClientReady) {
        throw new Error("Для самостоятельного кабинета должен быть назначен ровно один активный клиент.");
      }
      const [overview, accountRows, connectionRows] = await Promise.all([
        req<IntegrationsOverview>("/integrations/overview"),
        req<{ items?: AdAccount[] }>("/ad-accounts?status=all"),
        req<{ items?: IntegrationConnection[] }>("/me/integration-connections?status=all"),
      ]);
      if (!isCurrentRequest()) return;
      if (
        !overview ||
        !overview.summary ||
        !Array.isArray(overview.providers) ||
        !Array.isArray(overview.events)
      ) {
        throw new Error("Сервис вернул некорректные данные о подключениях");
      }
      const allAccounts = Array.isArray(accountRows?.items) ? accountRows.items : [];
      const allowedClientIds = agencyContext.role === "agency" || soloClient
        ? new Set(agencyContext.clientIds)
        : null;
      const visibleAccounts = allowedClientIds
        ? allAccounts.filter((account) => allowedClientIds.has(account.client_id))
        : allAccounts;
      if (allowedClientIds) {
        const allConnections = Array.isArray(connectionRows?.items) ? connectionRows.items : [];
        const visibleConnections = allConnections.filter((connection) => (
          (agencyContext.role === "agency" && connection.scope_type === "agency" && connection.scope_id === agencyContext.selectedAgencyId)
          || (connection.scope_type === "client" && !!connection.scope_id && allowedClientIds.has(connection.scope_id))
        ));
        const scopedProviders = overview.providers.map((provider) => {
          const providerKey = provider.provider === "facebook" ? "meta" : provider.provider;
          const providerAccounts = visibleAccounts.filter((account) => {
            const accountKey = account.platform === "facebook" ? "meta" : account.platform;
            return accountKey === providerKey;
          });
          const providerConnections = visibleConnections.filter((connection) => {
            const connectionKey = connection.provider === "facebook" ? "meta" : connection.provider;
            return connectionKey === providerKey && connection.status === "active";
          });
          const activeAccounts = providerAccounts.filter((account) => account.status === "active");
          const errorAccounts = activeAccounts.filter((account) => account.sync_status === "error").length;
          const neverSynced = activeAccounts.filter((account) => !account.last_sync_at).length;
          const connected = providerConnections.length > 0;
          return {
            ...provider,
            status: connected ? (errorAccounts ? "error" : neverSynced ? "warning" : "healthy") : "disconnected",
            auth_state: connected ? "configured" : "missing",
            connection_sources: providerConnections.map((connection) => connection.connection_key),
            identity_linked_users: providerConnections.length,
            sync_ready: connected,
            linked_accounts_count: providerAccounts.length,
            active_accounts_count: activeAccounts.length,
            successfully_synced_accounts_count: activeAccounts.filter((account) => account.sync_status === "success").length,
            error_accounts_count: errorAccounts,
            never_synced_accounts_count: neverSynced,
            affected_clients_count: new Set(providerAccounts.map((account) => account.client_id)).size,
          } satisfies IntegrationProvider;
        });
        const connectedProviders = scopedProviders.filter((provider) => provider.auth_state === "configured").length;
        setData({
          summary: {
            ...overview.summary,
            connected_providers: connectedProviders,
            healthy_connections: scopedProviders.filter((provider) => provider.status === "healthy").length,
            warning_connections: scopedProviders.filter((provider) => provider.status === "warning").length,
            critical_issues: scopedProviders.filter((provider) => provider.status === "error").length,
            active_nodes: visibleAccounts.filter((account) => account.status === "active").length,
          },
          providers: scopedProviders,
          events: [],
        });
      } else {
        setData(overview);
      }
      setAccounts(visibleAccounts);
      setWarning("");
    } finally {
      setLoading(false);
    }
  }, [
    agencyContext.clientIds,
    agencyContext.portfolioError,
    agencyContext.portfolioReady,
    agencyContext.role,
    agencyContext.selectedAgencyId,
    agencyContext.selectionRequired,
    agencyContext.soloClientReady,
    beginScopedRequest,
    req,
    soloClient,
  ]);

  useEffect(() => {
    if (!ready || agencyContext.loading) return;
    void loadData().catch((err) =>
      setWarning(err instanceof Error ? err.message : "Не удалось загрузить источники рекламы")
    );
  }, [agencyContext.loading, ready, loadData]);

  useEffect(() => {
    setData(null);
    setAccounts([]);
    setSelectedProvider("");
  }, [agencyContext.selectedAgencyId]);

  const providers = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = Array.isArray(data?.providers) ? data.providers : [];
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
  const selectedDataMeta = useMemo(
    () => selected ? dataFreshnessMeta(providerDataFreshness(selected)) : null,
    [selected]
  );

  const runProviderSync = useCallback(async () => {
    if (!selected?.provider || syncLoading) return;
    const scopedAccountIds = accounts
      .filter((account) => {
        const accountProvider = account.platform === "facebook" ? "meta" : account.platform;
        const selectedKey = selected.provider === "facebook" ? "meta" : selected.provider;
        return accountProvider === selectedKey;
      })
      .map((account) => account.id);
    if (
      (agencyContext.role === "agency" && !agencyContext.portfolioReady)
      || (soloClient && !agencyContext.soloClientReady)
      || ((agencyContext.role === "agency" || soloClient) && !scopedAccountIds.length)
    ) {
      push(
        soloClient
          ? "В вашем кабинете нет аккаунтов этой платформы для обновления."
          : "У выбранного агентства нет аккаунтов этой платформы для обновления.",
        "info",
      );
      return;
    }
    setSyncLoading(true);
    setWarning("");
    try {
      const result = await req<AdAccountSyncRunResponse>("/ad-accounts/sync/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform: selected.provider,
          ...(agencyContext.role === "agency" || soloClient ? { account_ids: scopedAccountIds } : {}),
          ...(soloClient ? { client_id: agencyContext.managedClientId } : {}),
        }),
      });
      await loadData();
      const feedback = syncRunFeedback(result);
      push(feedback.message, feedback.tone);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Не удалось запустить синхронизацию";
      setWarning(message);
      push(message, "error");
    } finally {
      setSyncLoading(false);
    }
  }, [accounts, agencyContext.managedClientId, agencyContext.portfolioReady, agencyContext.role, agencyContext.soloClientReady, loadData, push, req, selected, soloClient, syncLoading]);

  const recentEvents = useMemo(() => {
    const items = Array.isArray(data?.events) ? data.events : [];
    if (!selected?.provider) return items.slice(0, 8);
    return items.filter((e) => e.provider === selected.provider).slice(0, 8);
  }, [data, selected]);

  const setup = useMemo(() => {
    const connected = data?.summary?.connected_providers ?? 0;
    const accountCount = accounts.length;
    const unassigned = accounts.filter((account) => !account.client_id).length;
    const issues = (data?.providers || []).filter(
      (provider) => providerDataFreshness(provider) !== "current"
    ).length;
    const completed = [
      connected > 0,
      accountCount > 0,
      accountCount > 0 && unassigned === 0,
      connected > 0 && accountCount > 0 && issues === 0,
    ].filter(Boolean).length;

    if (!connected) {
      return {
        connected,
        accountCount,
        unassigned,
        issues,
        completed,
        title: "Подключите первую рекламную платформу",
        description: "Начните с Meta или Google Ads. После авторизации мы найдём доступные рекламные аккаунты.",
        action: "Подключить платформу",
        href: "/sync-monitor#provider-connections",
      };
    }
    if (!accountCount) {
      return {
        connected,
        accountCount,
        unassigned,
        issues,
        completed,
        title: "Импортируйте рекламные аккаунты",
        description: "Подключение уже работает. Найдите кабинеты, к которым у вас есть доступ.",
        action: "Найти аккаунты",
        href: "/sync-monitor#provider-connections",
      };
    }
    if (unassigned > 0) {
      return {
        connected,
        accountCount,
        unassigned,
        issues,
        completed,
        title: soloClient ? "Завершите привязку аккаунтов" : "Распределите аккаунты по клиентам",
        description: soloClient
          ? "Некоторые найденные аккаунты ещё не попали в ваш кабинет. Повторите поиск или обратитесь к администратору."
          : `Без клиента осталось аккаунтов: ${unassigned}. Назначьте клиентов, чтобы данные попали в правильные отчёты.`,
        action: soloClient ? "Проверить аккаунты" : "Распределить аккаунты",
        href: "/accounts",
      };
    }
    if (issues > 0) {
      return {
        connected,
        accountCount,
        unassigned,
        issues,
        completed,
        title: "Проверьте качество синхронизации",
        description: "Есть подключение, которое требует внимания. Диагностика подскажет конкретное действие.",
        action: "Открыть диагностику",
        href: "/sync-monitor#sync-diagnostics",
      };
    }
    return {
      connected,
      accountCount,
      unassigned,
      issues,
      completed: 4,
      title: "Источники настроены",
      description: "Аккаунты привязаны к клиентам, и у каждой подключённой платформы есть свежая успешная загрузка.",
      action: "Открыть рекламные аккаунты",
      href: "/accounts",
    };
  }, [accounts, data, soloClient]);

  const setupSteps = [
    { label: "Платформа подключена", done: setup.connected > 0 },
    { label: "Аккаунты импортированы", done: setup.accountCount > 0 },
    { label: soloClient ? "Аккаунты закреплены за вашим кабинетом" : "Клиенты назначены", done: setup.accountCount > 0 && setup.unassigned === 0 },
    {
      label: "Есть свежая успешная загрузка",
      done: setup.connected > 0 && setup.accountCount > 0 && setup.issues === 0,
    },
  ];
  const assignmentConflictCount = data?.summary?.assignment_conflict_accounts
    ?? (data?.providers || []).reduce(
      (total, item) => total + (item.assignment_conflict_accounts_count || 0),
      0,
    );

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="integrations" />

        <main className="content">
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs active="integrations" sectionLabel="Источники рекламы" />
              <div className="topbar-title">Источники рекламы</div>
              <div className="panel-subtitle">
                Подключение платформ, импорт рекламных аккаунтов и контроль обновления данных в одном месте
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

          <DataSourcesNav active="overview" />

          {!soloClient && assignmentConflictCount > 0 ? (
            <section className="alert-card high" style={{ marginTop: 12 }}>
              <div className="alert-priority high">ТРЕБУЕТСЯ РЕШЕНИЕ</div>
              <div className="insight-text" style={{ marginTop: 8 }}>
                Конфликтующих рекламных аккаунтов: {assignmentConflictCount}. Они временно исключены из обновления и отчётов, чтобы данные разных клиентов не смешались.
              </div>
              <Link className="primary-btn" href="/accounts#assignment-conflicts" style={{ marginTop: 10 }}>
                Выбрать правильных владельцев
              </Link>
            </section>
          ) : null}

          <section className="data-setup-hero" aria-labelledby="data-setup-title">
            <div className="data-setup-main">
              <div className="data-setup-overline">Быстрый старт · {setup.completed} из 4 шагов</div>
              <h2 id="data-setup-title">{loading ? "Проверяем подключения…" : setup.title}</h2>
              <p>{loading ? "Собираем состояние платформ и рекламных аккаунтов." : setup.description}</p>
              <div className="data-setup-progress" aria-label={`Выполнено ${setup.completed} из 4 шагов`}>
                <span style={{ width: `${(setup.completed / 4) * 100}%` }} />
              </div>
              <div className="data-setup-steps">
                {setupSteps.map((step, index) => (
                  <div className={step.done ? "done" : ""} key={step.label}>
                    <span>{step.done ? "✓" : index + 1}</span>
                    {step.label}
                  </div>
                ))}
              </div>
            </div>
            <div className="data-setup-action">
              <Link className="primary-btn" href={setup.href} aria-disabled={loading}>
                {loading ? "Загрузка…" : setup.action}
              </Link>
              <small>Система подсказывает только следующий необходимый шаг</small>
            </div>
          </section>

          <section className="data-source-kpis" aria-label="Состояние источников">
            <article>
              <span>Платформы</span>
              <strong>{data?.summary?.connected_providers ?? 0}</strong>
              <small>
                {(data?.providers || []).filter((provider) => providerDataFreshness(provider) === "current").length} с актуальными данными; подключение само по себе не означает свежесть
              </small>
            </article>
            <article>
              <span>Рекламные аккаунты</span>
              <strong>{accounts.length}</strong>
              <small>доступны в рабочем пространстве</small>
            </article>
            <article className={!soloClient && setup.unassigned ? "warn" : ""}>
              <span>{soloClient ? "В вашем кабинете" : "Без клиента"}</span>
              <strong>{soloClient ? setup.accountCount : setup.unassigned}</strong>
              <small>
                {soloClient
                  ? setup.unassigned ? "нужно проверить привязку" : "все аккаунты закреплены"
                  : setup.unassigned ? "нужно распределить" : "все аккаунты распределены"}
              </small>
            </article>
            <article className={setup.issues ? "bad" : ""}>
              <span>Требуют внимания</span>
              <strong>{setup.issues}</strong>
              <small>{setup.issues ? "есть понятное действие" : "критических проблем нет"}</small>
            </article>
          </section>

          <section className="data-sources-layout">
            <article className="panel data-providers-panel">
              <div className="panel-head">
                <div>
                  <h3 style={{ margin: 0 }}>Рекламные платформы</h3>
                  <div className="panel-subtitle">Выберите платформу, чтобы увидеть состояние и доступные действия</div>
                </div>
                <Link className="ghost-btn" href="/sync-monitor#provider-connections">+ Подключить</Link>
              </div>
              <label className="data-provider-search">
                <span className="sr-only">Найти платформу</span>
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Найти платформу"
                />
              </label>
              <div className="data-provider-list">
                {providers.map((provider) => {
                  const active = selectedProvider === provider.provider;
                  return (
                    <button
                      type="button"
                      className={`data-provider-card ${active ? "active" : ""}`.trim()}
                      key={provider.provider}
                      onClick={() => setSelectedProvider(provider.provider)}
                      aria-pressed={active}
                    >
                      <span className={`data-provider-mark ${statusClass(provider)}`}>
                        {providerMark(provider.provider)}
                      </span>
                      <span className="data-provider-copy">
                        <span className="data-provider-title">
                          <strong>{providerLabel(provider.provider)}</strong>
                          <span className={`badge ${statusClass(provider)}`}>
                            {statusLabel(provider)}
                          </span>
                        </span>
                        <small>
                          {accountCountLabel(provider.active_accounts_count ?? provider.linked_accounts_count)} · {provider.last_successful_sync_at ? `последняя успешная загрузка ${fmtDate(provider.last_successful_sync_at)}` : "успешных загрузок ещё не было"}
                        </small>
                      </span>
                      <span className="data-provider-chevron">›</span>
                    </button>
                  );
                })}
                {!providers.length && !loading ? (
                  <div className="data-empty-state">
                    <strong>Платформы ещё не подключены</strong>
                    <span>Подключите Meta или Google Ads — мы автоматически найдём рекламные аккаунты.</span>
                    <Link className="primary-btn" href="/sync-monitor#provider-connections">Подключить платформу</Link>
                  </div>
                ) : null}
              </div>
            </article>

            <aside className="panel data-provider-detail">
              {!selected ? (
                <div className="data-empty-state compact">
                  <strong>Выберите платформу</strong>
                  <span>Справа появятся состояние подключения и следующие действия.</span>
                </div>
              ) : (
                <>
                  <div className="data-provider-detail-head">
                    <span className={`data-provider-mark large ${statusClass(selected)}`}>
                      {providerMark(selected.provider)}
                    </span>
                    <div>
                      <div className="kpi-title">Выбранная платформа</div>
                      <h3>{providerLabel(selected.provider)}</h3>
                    </div>
                    <span className={`badge ${statusClass(selected)}`}>{statusLabel(selected)}</span>
                  </div>

                  <div className="data-provider-summary">
                    <div>
                      <span>Авторизация</span>
                      <strong>{authStateLabel(selected.auth_state)}</strong>
                    </div>
                    <div>
                      <span>Рекламные аккаунты</span>
                      <strong>{selected.linked_accounts_count}</strong>
                    </div>
                    <div>
                      <span>Последняя успешная загрузка</span>
                      <strong>{fmtDate(selected.last_successful_sync_at)}</strong>
                    </div>
                    <div>
                      <span>Затронуто клиентов</span>
                      <strong>{selected.affected_clients_count}</strong>
                    </div>
                  </div>

                  {!selected.sync_ready || selected.last_error_safe || selectedDataMeta?.tone !== "good" ? (
                    <div className="data-next-step">
                      <strong>{!selected.sync_ready ? "Нужна настройка" : selectedDataMeta?.label || "Что сделать"}</strong>
                      <span>
                        {(selected.assignment_conflict_accounts_count || 0) > 0
                          ? `Найдено конфликтующих привязок: ${selected.assignment_conflict_accounts_count}. Откройте монитор обновлений и оставьте рекламный аккаунт только у одного активного клиента.`
                          : selected.last_error_safe
                          ? readableEventMessage(selected.last_error_safe)
                          : !selected.sync_ready
                            ? "Завершите авторизацию платформы, чтобы начать загрузку данных."
                            : selectedDataMeta?.description}
                      </span>
                    </div>
                  ) : (
                    <div className="data-next-step success">
                      <strong>Данные актуальны</strong>
                      <span>Подключение настроено, а последняя успешная загрузка подтверждена и свежая.</span>
                    </div>
                  )}

                  <details className="data-technical-details">
                    <summary>Техническая информация</summary>
                    <dl>
                      <div>
                        <dt>Источник авторизации</dt>
                        <dd>{selected.connection_sources?.join(", ") || "Не указан"}</dd>
                      </div>
                      <div>
                        <dt>Разрешения</dt>
                        <dd>{selected.scopes?.join(", ") || "Не указаны"}</dd>
                      </div>
                      <div>
                        <dt>Недостаёт</dt>
                        <dd>{selected.missing_requirements?.join(", ") || "Ничего"}</dd>
                      </div>
                    </dl>
                  </details>

                  <div className="data-provider-actions">
                    <Link className="primary-btn" href="/sync-monitor#provider-connections">
                      Управлять подключением
                    </Link>
                    <button
                      className="ghost-btn"
                      onClick={() => void runProviderSync()}
                      disabled={syncLoading || !selected.sync_ready}
                      title={!selected.sync_ready ? "Сначала завершите настройку подключения" : undefined}
                    >
                      {syncLoading ? "Обновляем…" : "Обновить данные"}
                    </button>
                  </div>
                </>
              )}
            </aside>
          </section>

          <section className="panel data-events-panel">
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>Последние события</h3>
                <div className="panel-subtitle">
                  {selected ? `Только ${providerLabel(selected.provider)}` : "По всем рекламным платформам"}
                </div>
              </div>
              <Link className="ghost-btn" href="/sync-monitor">Вся история</Link>
            </div>
            <div className="data-events-list">
              {recentEvents.length ? recentEvents.map((event) => (
                <div className="data-event-row" key={`${event.provider}-${event.title}-${event.occurred_at}`}>
                  <span className={`data-event-dot ${event.level}`} />
                  <div>
                    <strong>{readableEventTitle(event.title)}</strong>
                    <span>{readableEventMessage(event.message)}</span>
                  </div>
                  <div className="data-event-meta">
                    <span>{providerLabel(event.provider)}</span>
                    <time dateTime={event.occurred_at}>{fmtDate(event.occurred_at)}</time>
                  </div>
                </div>
              )) : (
                <div className="data-empty-state compact">
                  <strong>Событий пока нет</strong>
                  <span>Здесь появятся результаты синхронизации и подсказки по ошибкам.</span>
                </div>
              )}
            </div>
          </section>
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
