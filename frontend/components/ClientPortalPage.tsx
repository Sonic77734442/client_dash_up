"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "./AppSidebar";
import { AppTopTabs } from "./AppTopTabs";
import { ProviderBudgetControl } from "./ProviderBudgetControl";
import { TimelineChart } from "./TimelineChart";
import { ToastHost } from "./ToastHost";
import { useSession } from "../hooks/useSession";
import { useToast } from "../hooks/useToast";
import { fetchJson, getQuery } from "../lib/api";
import { accountDataFreshness, aggregateAccountFreshness, dataFreshnessMeta, overviewDataFreshness } from "../lib/dataFreshness";
import { normalizeOverviewPayload } from "../lib/analyticsPayload";
import {
  hasOptionalStringFields,
  hasStringFields,
  normalizeListPayload,
} from "../lib/listPayload";
import {
  AdAccount,
  AdStat,
  AuthMeResponse,
  Budget,
  ClientOut,
  OperationalAction,
  OperationalInsight,
  Overview,
  TimelinePoint,
} from "../lib/types";

export type ClientPortalTab = "overview" | "advertising" | "leads" | "reports" | "changes" | "plan" | "billing";
type PlatformKey = "google" | "meta" | "tiktok";
type Tone = "good" | "warn" | "bad" | "";

const PLATFORMS: Array<{ key: PlatformKey; label: string }> = [
  { key: "google", label: "Google Ads" },
  { key: "meta", label: "Meta Ads" },
  { key: "tiktok", label: "TikTok Ads" },
];

const TAB_META: Record<ClientPortalTab, { title: string; subtitle: string }> = {
  overview: { title: "Результаты рекламы", subtitle: "Главные показатели, отклонения и необходимые действия" },
  advertising: { title: "Реклама", subtitle: "Результаты по рекламным площадкам и аккаунтам" },
  leads: { title: "Лиды", subtitle: "Конверсии, полученные из рекламных площадок" },
  reports: { title: "Отчёты", subtitle: "Понятный отчёт по фактическим данным выбранного периода" },
  changes: { title: "Что изменилось", subtitle: "Значимые отклонения и объяснения причин" },
  plan: { title: "План действий", subtitle: "Что агентство поставило в работу" },
  billing: { title: "Бюджет", subtitle: "Расход, остаток и прогноз по рекламному бюджету" },
};

const TAB_LABELS: Record<ClientPortalTab, string> = {
  overview: "Главное",
  advertising: "Реклама",
  leads: "Лиды",
  reports: "Отчёты",
  changes: "Что изменилось",
  plan: "План действий",
  billing: "Бюджет",
};

function isClientItem(value: unknown): value is ClientOut {
  return hasStringFields(value, ["id", "name"]);
}

function isAdAccountItem(value: unknown): value is AdAccount {
  return (
    hasStringFields(value, ["id", "client_id", "platform", "name"]) &&
    hasOptionalStringFields(value, ["external_account_id", "currency", "last_sync_at"])
  );
}

function isBudgetItem(value: unknown): value is Budget {
  return (
    hasStringFields(value, ["client_id", "scope", "amount"]) &&
    hasOptionalStringFields(value, ["currency", "start_date", "end_date", "status"])
  );
}

function isActionItem(value: unknown): value is OperationalAction {
  return hasStringFields(value, [
    "id",
    "action",
    "scope",
    "scope_id",
    "status",
    "title",
    "created_at",
  ]);
}

function isAdStatItem(value: unknown): value is AdStat {
  return hasStringFields(value, ["date", "platform"]);
}

function isInsightItem(value: unknown): value is OperationalInsight {
  return hasStringFields(value, ["scope", "scope_id", "title", "reason", "action", "priority"]);
}

function fmtMoney(value: number | null | undefined, currency = "USD") {
  const safeCurrency = /^[A-Z]{3}$/.test(currency) ? currency : "USD";
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency: safeCurrency,
      maximumFractionDigits: 0,
    }).format(Number(value || 0));
  } catch {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Number(value || 0));
  }
}

function fmtNum(value: number | null | undefined) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Number(value || 0));
}

function fmtRate(value: number | null | undefined) {
  const raw = Number(value || 0);
  const percent = Math.abs(raw) <= 1 ? raw * 100 : raw;
  return `${percent.toFixed(2).replace(".", ",")}%`;
}

function fmtDate(value?: string | null) {
  if (!value) return "Нет данных";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Нет данных";
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtShortDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" });
}

function dateRange(periodDays: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (periodDays - 1));
  const fmt = (date: Date) => date.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

function platformLabel(platform: string) {
  return PLATFORMS.find((item) => item.key === platform)?.label || platform;
}

function actionLabel(action: string) {
  const labels: Record<string, string> = {
    scale: "Увеличить бюджет",
    cap: "Ограничить расход",
    pause: "Приостановить",
    review: "Проверить стратегию",
  };
  return labels[String(action || "").toLowerCase()] || action || "Проверить";
}

function actionTitle(action: Pick<OperationalAction, "action" | "title">) {
  const title = String(action.title || "").trim();
  return title && /[А-Яа-яЁё]/.test(title) ? title : actionLabel(action.action);
}

function actionStatus(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "applied" || normalized === "completed" || normalized === "done") {
    return { label: "Выполнено", tone: "good" as Tone };
  }
  if (normalized === "failed" || normalized === "error") {
    return { label: "Ошибка", tone: "bad" as Tone };
  }
  return { label: "В работе", tone: "warn" as Tone };
}

function priorityMeta(priority: string) {
  if (priority === "high") return { label: "Критично", tone: "bad" as Tone, rank: 3 };
  if (priority === "medium") return { label: "Внимание", tone: "warn" as Tone, rank: 2 };
  return { label: "Наблюдение", tone: "good" as Tone, rank: 1 };
}

function insightCopy(insight: OperationalInsight, currency: string) {
  const metric = (key: string) => Number(insight.metrics?.[key] || 0);
  const platform = String(insight.metrics?.platform || "").toUpperCase();

  if (insight.metrics?.fallback) {
    return {
      title: "Срочных действий не требуется",
      reason: "Показатели находятся внутри заданных порогов. Продолжайте наблюдение.",
    };
  }
  if (insight.action === "cap") {
    return {
      title: `Ограничить расход${platform ? ` в ${platform}` : ""}`,
      reason: `Стоимость клика ${fmtMoney(metric("cpc"), currency)} выше обычного уровня, а аккаунт формирует ${(metric("spend_share") * 100).toFixed(1).replace(".", ",")}% расхода.`,
    };
  }
  if (insight.action === "scale") {
    return {
      title: `Рассмотреть масштабирование${platform ? ` в ${platform}` : ""}`,
      reason: `CTR ${(metric("ctr") * 100).toFixed(2).replace(".", ",")}% выше среднего, стоимость клика остаётся эффективной.`,
    };
  }
  if (insight.metrics?.pace_delta_percent != null) {
    return {
      title: "Проверить темп расходования бюджета",
      reason: `Фактический темп отличается от ожидаемого на ${metric("pace_delta_percent").toFixed(1).replace(".", ",")}%.`,
    };
  }
  if (insight.action === "review" && insight.metrics?.ctr != null) {
    return {
      title: "Проверить объявления и креативы",
      reason: `CTR ${(metric("ctr") * 100).toFixed(2).replace(".", ",")}% ниже среднего уровня по сопоставимым аккаунтам.`,
    };
  }
  return { title: insight.title, reason: insight.reason };
}

function paceMeta(status?: string | null) {
  const normalized = String(status || "on_track").toLowerCase();
  if (normalized.includes("over") || normalized.includes("critical")) {
    return {
      label: "Расход выше темпа",
      tone: "bad" as Tone,
      description: "При текущем темпе есть риск превысить доступный бюджет.",
    };
  }
  if (normalized.includes("under") || normalized.includes("slow")) {
    return {
      label: "Расход ниже темпа",
      tone: "warn" as Tone,
      description: "Бюджет расходуется медленнее ожидаемого темпа.",
    };
  }
  if (normalized.includes("no_budget")) {
    return {
      label: "План не задан",
      tone: "" as Tone,
      description: "Бюджетный план для выбранного периода не задан.",
    };
  }
  return {
    label: "В пределах плана",
    tone: "good" as Tone,
    description: "Расход соответствует доступному бюджетному плану.",
  };
}

function accountStatus(account: AdAccount) {
  const meta = dataFreshnessMeta(accountDataFreshness(account));
  return { label: meta.label, tone: meta.tone as Tone, description: meta.description };
}

function MetricCard({
  title,
  value,
  note,
  tone = "",
}: {
  title: string;
  value: string;
  note: string;
  tone?: Tone;
}) {
  return (
    <article className={`kpi-card ${tone}`.trim()}>
      <div className="kpi-title">{title}</div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-meta" style={{ display: "block" }}>{note}</div>
    </article>
  );
}

export function ClientPortalPage({ activeTab }: { activeTab: ClientPortalTab }) {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [periodDays, setPeriodDays] = useState(30);
  const [warning, setWarning] = useState("");
  const [loading, setLoading] = useState(false);
  const [soloClientMode, setSoloClientMode] = useState(false);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [selectedClientId, setSelectedClientId] = useState("");
  const [activePlatform, setActivePlatform] = useState<PlatformKey>("google");
  const [selectedAccountId, setSelectedAccountId] = useState("");

  const [overview, setOverview] = useState<Overview | null>(null);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [actions, setActions] = useState<OperationalAction[]>([]);
  const [stats, setStats] = useState<AdStat[]>([]);
  const [insights, setInsights] = useState<OperationalInsight[]>([]);

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadContext = useCallback(async () => {
    const payload = await fetchJson<AuthMeResponse>(session.apiBase, "/auth/me", session.token);
    return payload.session;
  }, [session.apiBase, session.token]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const context = await loadContext();
      if (!context?.valid) {
        setWarning("Сессия недействительна или истекла. Войдите снова.");
        return;
      }
      if (context.role !== "client" && context.role !== "solo_client") {
        setWarning("Этот кабинет доступен только клиенту. Для администратора и агентства используется рабочая консоль.");
        return;
      }

      const availableIds = Array.isArray(context.accessible_client_ids)
        ? context.accessible_client_ids.filter((id): id is string => typeof id === "string")
        : [];
      if (context.role === "solo_client" && new Set(availableIds).size !== 1) {
        setClients([]);
        setSelectedClientId("");
        setOverview(null);
        setAccounts([]);
        setBudgets([]);
        setActions([]);
        setStats([]);
        setInsights([]);
        setWarning("Для самостоятельного кабинета должен быть назначен ровно один активный клиент.");
        return;
      }
      setSoloClientMode(context.role === "solo_client");

      const clientPayload = await req<unknown>("/clients?status=active");
      const availableClients = normalizeListPayload(
        clientPayload,
        isClientItem,
        "клиентов",
      ).filter((client) => availableIds.includes(client.id));
      setClients(availableClients);

      const clientId =
        selectedClientId && availableClients.some((client) => client.id === selectedClientId)
          ? selectedClientId
          : availableClients[0]?.id || "";
      setSelectedClientId(clientId);
      if (!clientId) {
        setOverview(null);
        setAccounts([]);
        setBudgets([]);
        setActions([]);
        setStats([]);
        setInsights([]);
        setWarning("Для этого пользователя не назначен клиент.");
        return;
      }

      const range = dateRange(periodDays);
      const query = getQuery({ client_id: clientId, date_from: range.from, date_to: range.to });
      const [overviewPayload, accountPayload, budgetPayload, actionPayload, statPayload, insightPayload] =
        await Promise.all([
          req<unknown>(`/insights/overview${query}`),
          req<unknown>(`/ad-accounts${getQuery({ client_id: clientId, status: "active" })}`),
          req<unknown>(
            `/budgets${getQuery({ client_id: clientId, status: "active", date_from: range.from, date_to: range.to })}`
          ),
          req<unknown>(`/insights/operational/actions${getQuery({ client_id: clientId })}`),
          req<unknown>(`/ad-stats${query}`),
          req<unknown>(`/insights/operational${query}`),
        ]);

      const nextOverview = normalizeOverviewPayload(overviewPayload);
      const nextAccounts = normalizeListPayload(accountPayload, isAdAccountItem, "рекламных аккаунтов");
      const nextBudgets = normalizeListPayload(budgetPayload, isBudgetItem, "бюджетов");
      const nextActions = normalizeListPayload(actionPayload, isActionItem, "действий").slice(0, 20);
      const nextStats = normalizeListPayload(statPayload, isAdStatItem, "статистики");
      const nextInsights = normalizeListPayload(insightPayload, isInsightItem, "рекомендаций");

      setOverview(nextOverview);
      setAccounts(nextAccounts);
      setBudgets(nextBudgets);
      setActions(nextActions);
      setStats(nextStats);
      setInsights(nextInsights);
      setWarning("");
    } finally {
      setLoading(false);
    }
  }, [loadContext, periodDays, req, selectedClientId]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((error) => {
      const message = error instanceof Error ? error.message : "Не удалось загрузить кабинет.";
      if (/unauthorized|401/i.test(message)) {
        setWarning("Сессия истекла. Перенаправляем на страницу входа…");
        window.location.replace("/login");
        return;
      }
      setWarning(message);
    });
  }, [ready, loadData]);

  useEffect(() => {
    setSelectedAccountId("");
  }, [activePlatform, selectedClientId]);

  useEffect(() => {
    const requestedPlatform = new URLSearchParams(window.location.search).get("platform");
    if (requestedPlatform === "google" || requestedPlatform === "meta" || requestedPlatform === "tiktok") {
      setActivePlatform(requestedPlatform);
    }
  }, []);

  const selectedClient = useMemo(
    () => clients.find((client) => client.id === selectedClientId) || null,
    [clients, selectedClientId]
  );
  const currency =
    selectedClient?.default_currency ||
    budgets.find((budget) => budget.currency)?.currency ||
    accounts.find((account) => account.currency)?.currency ||
    "USD";

  const spendByDate = useMemo(() => {
    const rows = new Map<string, number>();
    for (const stat of stats) {
      rows.set(stat.date, Number(rows.get(stat.date) || 0) + Number(stat.spend || 0));
    }
    return [...rows.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([date, spend]) => ({ date, spend }));
  }, [stats]);

  const timelinePoints = useMemo<TimelinePoint[]>(() => {
    const overviewRange = overview?.range;
    if (!overviewRange?.date_from || !overviewRange.date_to || !spendByDate.length) return [];
    const start = new Date(`${overviewRange.date_from}T00:00:00Z`);
    const end = new Date(`${overviewRange.date_to}T00:00:00Z`);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return [];

    const dates: string[] = [];
    const cursor = new Date(start);
    while (cursor <= end && dates.length < 370) {
      dates.push(cursor.toISOString().slice(0, 10));
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }

    const dailySpend = new Map(spendByDate.map((row) => [row.date, row.spend]));
    const budget = Number(overview.budget_summary?.budget || 0);
    const asOf = overviewRange.as_of_date || overviewRange.date_to;
    let cumulative = 0;
    return dates.map((date, index) => {
      cumulative += Number(dailySpend.get(date) || 0);
      return {
        date,
        label: fmtShortDate(date),
        expected: budget > 0 ? (budget / Math.max(1, dates.length)) * (index + 1) : 0,
        actual: date <= asOf ? cumulative : null,
      };
    });
  }, [overview, spendByDate]);

  const timelineActions = useMemo(
    () =>
      actions
        .filter((action) => Boolean(action.created_at))
        .map((action) => ({
          date: action.created_at.slice(0, 10),
          action: actionLabel(action.action),
          title: actionTitle(action),
        })),
    [actions]
  );

  const conversionsByPlatform = useMemo(() => {
    const rows = new Map<string, { spend: number; conversions: number }>();
    for (const stat of stats) {
      const current = rows.get(stat.platform) || { spend: 0, conversions: 0 };
      current.spend += Number(stat.spend || 0);
      current.conversions += Number(stat.conversions || 0);
      rows.set(stat.platform, current);
    }
    return [...rows.entries()]
      .map(([platform, values]) => ({
        platform,
        ...values,
        cpl: values.conversions > 0 ? values.spend / values.conversions : null,
      }))
      .filter((row) => row.conversions > 0)
      .sort((a, b) => b.conversions - a.conversions);
  }, [stats]);

  const platformRows = overview?.breakdowns?.platforms || [];
  const accountRows = overview?.breakdowns?.accounts || [];
  const platformAccounts = accounts.filter((account) => account.platform === activePlatform);
  const platformAccountIds = new Set(platformAccounts.map((account) => account.id));
  const activePlatformRow = platformRows.find((row) => row.platform === activePlatform) || null;
  const selectedAccountRow =
    accountRows.find((row) => row.account_id === selectedAccountId && row.platform === activePlatform) || null;
  const advertisingScope = selectedAccountRow || activePlatformRow;
  const advertisingCpl =
    advertisingScope && advertisingScope.conversions > 0
      ? advertisingScope.spend / advertisingScope.conversions
      : null;

  const rankedInsights = useMemo(
    () => [...insights].sort((a, b) => priorityMeta(b.priority).rank - priorityMeta(a.priority).rank),
    [insights]
  );
  const actionableInsights = useMemo(
    () => rankedInsights.filter((insight) => !insight.metrics?.fallback),
    [rankedInsights]
  );
  const headlineInsight = rankedInsights[0] || null;
  const headlineChange = actionableInsights[0] || null;
  const advertisingInsight =
    rankedInsights.find((insight) =>
      selectedAccountId
        ? insight.scope === "account" && insight.scope_id === selectedAccountId
        : insight.scope === "account" && platformAccountIds.has(insight.scope_id)
    ) || null;
  const headlineInsightCopy = headlineInsight ? insightCopy(headlineInsight, currency) : null;
  const headlineChangeCopy = headlineChange ? insightCopy(headlineChange, currency) : null;
  const advertisingInsightCopy = advertisingInsight ? insightCopy(advertisingInsight, currency) : null;
  const headlineAction =
    actions.find((action) => actionStatus(action.status).tone === "warn") || actions[0] || null;
  const dataState = overviewDataFreshness(overview);
  const dataMeta = dataFreshnessMeta(dataState);
  const rawPace = paceMeta(overview?.budget_summary?.pace_status);
  const pace = dataState === "current"
    ? rawPace
    : { label: dataMeta.label, description: dataMeta.description, tone: dataMeta.tone as Tone };
  const totalSpend = Number(overview?.spend_summary?.spend || 0);
  const totalLeads = Number(overview?.spend_summary?.conversions || 0);
  const totalCpl = totalLeads > 0 ? totalSpend / totalLeads : null;
  const rawBudgetUsage = overview?.budget_summary?.usage_percent;
  const budgetUsage =
    rawBudgetUsage == null || !Number.isFinite(Number(rawBudgetUsage))
      ? null
      : Number(rawBudgetUsage);
  const periodLabel = overview?.range
    ? `${fmtShortDate(overview.range.date_from)} — ${fmtShortDate(overview.range.date_to)}`
    : `${periodDays} дней`;

  const summaryText = dataState !== "current"
    ? dataMeta.description
    : totalSpend
    ? `За ${periodLabel} рекламные площадки зафиксировали ${fmtNum(totalLeads)} конверсий при расходе ${fmtMoney(
        totalSpend,
        currency
      )}. ${pace.description}`
    : `За ${periodLabel} рекламные площадки не вернули расход и конверсии. Проверьте период или состояние подключений.`;

  const describeScope = (scope: string, scopeId: string) => {
    if (scope === "client") return selectedClient?.name || "Клиент";
    if (scope === "account") {
      return accounts.find((account) => account.id === scopeId)?.name || "Рекламный аккаунт";
    }
    if (scope === "agency") return "Агентство";
    return "Рекламный объект";
  };

  const planCounts = actions.reduce(
    (result, action) => {
      const meta = actionStatus(action.status);
      if (meta.tone === "good") result.completed += 1;
      else if (meta.tone === "bad") result.failed += 1;
      else result.inProgress += 1;
      return result;
    },
    { inProgress: 0, completed: 0, failed: 0 }
  );

  const tabMeta = TAB_META[activeTab];

  return (
    <>
      <div className="app-shell">
        <AppSidebar
          active="dashboard"
          subtitle={selectedClient?.name
            ? `${selectedClient.name} · ${soloClientMode ? "самостоятельный кабинет" : "клиентский кабинет"}`
            : soloClientMode ? "Самостоятельный кабинет" : "Клиентский кабинет"}
        />

        <main className="content">
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs
                active="dashboard"
                contextLabel={selectedClient?.name || "Клиент"}
                sectionLabel={TAB_LABELS[activeTab]}
              />
              <div className="topbar-title">{tabMeta.title}</div>
              <div className="panel-subtitle">
                {tabMeta.subtitle}
                {overview ? ` · ${periodLabel}` : ""}
              </div>
            </div>
            <div className="session-controls">
              {activeTab === "reports" ? (
                <button className="primary-btn" onClick={() => window.print()}>
                  Скачать PDF
                </button>
              ) : null}
              {tokenLoginEnabled ? (
                <details className="debug-session">
                  <summary>Локальная сессия</summary>
                  <div className="debug-session-popover">
                    <input
                      type="text"
                      value={session.apiBase}
                      onChange={(event) => setSession((current) => ({ ...current, apiBase: event.target.value }))}
                      placeholder="API base"
                    />
                    <input
                      type="password"
                      value={session.token}
                      onChange={(event) => setSession((current) => ({ ...current, token: event.target.value }))}
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
                        try {
                          await loadData();
                          push("Сессия сохранена", "success");
                        } catch (error) {
                          setWarning(error instanceof Error ? error.message : "Не удалось сохранить сессию.");
                        }
                      }}
                      disabled={!ready || loading}
                    >
                      Сохранить
                    </button>
                  </div>
                </details>
              ) : null}
            </div>
          </header>

          <section className="filters">
            <label>
              Период
              <select value={String(periodDays)} onChange={(event) => setPeriodDays(Number(event.target.value))}>
                <option value="7">Последние 7 дней</option>
                <option value="30">Последние 30 дней</option>
                <option value="90">Последние 90 дней</option>
              </select>
            </label>
            <label>
              Клиент
              <select value={selectedClientId} onChange={(event) => setSelectedClientId(event.target.value)}>
                {clients.map((client) => (
                  <option key={client.id} value={client.id}>
                    {client.name}
                  </option>
                ))}
                {!clients.length && selectedClientId ? <option value={selectedClientId}>Назначенный клиент</option> : null}
              </select>
            </label>
            <div className="asof">
              {overview ? `${dataMeta.label} · срез на ${fmtShortDate(overview.range.as_of_date)}` : "Данные загружаются"}
            </div>
            <button className="ghost-btn" onClick={() => void loadData()} disabled={loading}>
              {loading ? "Обновляем…" : "Обновить"}
            </button>
          </section>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>
          {overview && dataState !== "current" ? (
            <div className="warning">{dataMeta.description}</div>
          ) : null}

          {activeTab === "overview" ? (
            <>
              <section className="kpi-grid">
                <MetricCard
                  title="Расход"
                  value={fmtMoney(totalSpend, currency)}
                  note={
                    budgetUsage == null
                      ? "Бюджетный план не задан"
                      : `${budgetUsage.toFixed(1).replace(".", ",")}% доступного бюджета`
                  }
                  tone={pace.tone}
                />
                <MetricCard
                  title="Полученные лиды"
                  value={fmtNum(totalLeads)}
                  note="Конверсии по данным рекламных площадок"
                />
                <MetricCard
                  title="Стоимость лида"
                  value={totalCpl == null ? "Нет данных" : fmtMoney(totalCpl, currency)}
                  note="Расход ÷ конверсии; план CPL пока не задан"
                />
                <MetricCard
                  title="Темп бюджета"
                  value={pace.label}
                  note={
                    overview?.budget_summary?.forecast_spend == null
                      ? "Прогноз недоступен"
                      : `Прогноз: ${fmtMoney(overview?.budget_summary?.forecast_spend, currency)}`
                  }
                  tone={pace.tone}
                />
              </section>

              <section className="role-dashboard-grid">
                <article className="panel">
                  <div className="panel-head">
                    <div>
                      <h3>Расход относительно бюджета</h3>
                      <div className="panel-subtitle">Накопительный факт и ожидаемый темп за выбранный период</div>
                    </div>
                    <span className={`badge ${pace.tone}`.trim()}>{pace.label}</span>
                  </div>
                  <div className="chart">
                    <TimelineChart
                      points={timelinePoints}
                      budgetCap={overview?.budget_summary?.budget}
                      asOfDate={overview?.range?.as_of_date}
                      actions={timelineActions}
                    />
                  </div>
                </article>

                <aside className={`blueprint-note ${headlineInsight?.priority === "high" ? "bad" : ""}`.trim()}>
                  <h3>Главное за период</h3>
                  <p>{summaryText}</p>
                  {dataState === "current" && headlineInsight ? (
                    <>
                      <p>
                        <strong>{headlineInsightCopy?.title}</strong>
                        <br />
                        {headlineInsightCopy?.reason}
                      </p>
                      <span className={`badge ${priorityMeta(headlineInsight.priority).tone}`}>
                        {priorityMeta(headlineInsight.priority).label}
                      </span>
                    </>
                  ) : dataState === "current" ? (
                    <p>Значимых отклонений по текущим правилам не обнаружено.</p>
                  ) : (
                    <p>Оценка отклонений приостановлена до подтверждённого обновления данных.</p>
                  )}
                </aside>
              </section>

              <section className="role-dashboard-grid">
                <article className="panel">
                  <h3>Состояние площадок и аккаунтов</h3>
                  <div className="decision-list">
                    {PLATFORMS.map((platform) => {
                      const row = platformRows.find((item) => item.platform === platform.key);
                      const relatedAccounts = accounts.filter((account) => account.platform === platform.key);
                      const platformDataMeta = dataFreshnessMeta(
                        aggregateAccountFreshness(relatedAccounts, { hasMetricRows: Boolean(row) })
                      );
                      const tone: Tone = relatedAccounts.length ? platformDataMeta.tone as Tone : "";
                      return (
                        <div className="decision-row" key={platform.key}>
                          <span className={`decision-dot badge ${tone}`.trim()} aria-hidden="true">•</span>
                          <div>
                            <div className="decision-title">{platform.label}</div>
                            <div className="panel-subtitle">
                              {!relatedAccounts.length
                                ? "Не подключено"
                                : `${platformDataMeta.label} · ${relatedAccounts.length} аккаунт(а) · ${fmtMoney(row?.spend || 0, currency)} · ${fmtNum(
                                    row?.conversions || 0
                                  )} конверсий`}
                            </div>
                          </div>
                          <button
                            className="ghost-btn"
                            onClick={() => {
                              window.location.assign(`/portal/advertising?platform=${platform.key}`);
                            }}
                          >
                            Открыть
                          </button>
                        </div>
                      );
                    })}
                  </div>

                  <div className="budgets-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Аккаунт</th>
                          <th>Площадка</th>
                          <th>Состояние</th>
                          <th>Последние данные</th>
                        </tr>
                      </thead>
                      <tbody>
                        {accounts.map((account) => {
                          const status = accountStatus(account);
                          return (
                            <tr key={account.id}>
                              <td>{account.name || account.external_account_id}</td>
                              <td>{platformLabel(account.platform)}</td>
                              <td><span className={`badge ${status.tone}`}>{status.label}</span></td>
                              <td>{fmtDate(account.last_sync_at)}</td>
                            </tr>
                          );
                        })}
                        {!accounts.length ? (
                          <tr><td colSpan={4} className="muted-note">Рекламные аккаунты ещё не подключены.</td></tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                </article>

                <aside className="panel">
                  <h3>{soloClientMode ? "Что нужно сделать" : "Что делает агентство"}</h3>
                  {headlineAction ? (
                    <div className="insight-card">
                      <div className="insight-head">
                        <div className="insight-title">{actionTitle(headlineAction)}</div>
                        <span className={`badge ${actionStatus(headlineAction.status).tone}`}>
                          {actionStatus(headlineAction.status).label}
                        </span>
                      </div>
                      <div className="insight-text">
                        Объект: {describeScope(headlineAction.scope, headlineAction.scope_id)}
                      </div>
                      <div className="insight-meta">{fmtDate(headlineAction.created_at)}</div>
                    </div>
                  ) : (
                    <div className="blueprint-note">
                      <strong>Активных действий нет</strong>
                      <p>
                        {soloClientMode
                          ? "Когда появится необходимое действие, его состояние будет показано здесь."
                          : "Когда агентство поставит изменение в работу, его состояние появится здесь."}
                      </p>
                    </div>
                  )}
                </aside>
              </section>
            </>
          ) : null}

          {activeTab === "advertising" ? (
            <>
              <div className="platform-strip" aria-label="Рекламные площадки">
                {PLATFORMS.map((platform) => {
                  const count = accounts.filter((account) => account.platform === platform.key).length;
                  return (
                    <button
                      key={platform.key}
                      className={`chip-btn ${activePlatform === platform.key ? "active" : ""}`}
                      aria-pressed={activePlatform === platform.key}
                      onClick={() => setActivePlatform(platform.key)}
                    >
                      {platform.label} · {count ? `${count} аккаунт(а)` : "не подключено"}
                    </button>
                  );
                })}
              </div>

              <section className="filters" style={{ marginTop: 12 }}>
                <label>
                  Рекламный аккаунт
                  <select value={selectedAccountId} onChange={(event) => setSelectedAccountId(event.target.value)}>
                    <option value="">Все аккаунты {platformLabel(activePlatform)}</option>
                    {platformAccounts.map((account) => (
                      <option key={account.id} value={account.id}>{account.name || account.external_account_id}</option>
                    ))}
                  </select>
                </label>
                <div className="asof">
                  Показаны только фактические данные выбранной площадки
                </div>
              </section>

              <section className="kpi-grid">
                <MetricCard
                  title="Расход"
                  value={fmtMoney(advertisingScope?.spend || 0, currency)}
                  note={selectedAccountRow ? "Выбранный рекламный аккаунт" : platformLabel(activePlatform)}
                />
                <MetricCard
                  title="Полученные лиды"
                  value={fmtNum(advertisingScope?.conversions || 0)}
                  note="Конверсии рекламной площадки"
                />
                <MetricCard
                  title="Стоимость лида"
                  value={advertisingCpl == null ? "Нет данных" : fmtMoney(advertisingCpl, currency)}
                  note="Расход ÷ конверсии"
                />
                <MetricCard
                  title="CTR"
                  value={fmtRate(advertisingScope?.ctr || 0)}
                  note={`${fmtNum(advertisingScope?.clicks || 0)} кликов · ${fmtNum(
                    advertisingScope?.impressions || 0
                  )} показов`}
                />
              </section>

              <section className="role-dashboard-grid">
                <article className="panel">
                  <div className="panel-head">
                    <div>
                      <h3>Рекламные аккаунты {platformLabel(activePlatform)}</h3>
                      <div className="panel-subtitle">Выберите аккаунт, чтобы обновить показатели и объяснение</div>
                    </div>
                  </div>
                  <div className="budgets-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Аккаунт</th>
                          <th>Состояние</th>
                          <th>Расход</th>
                          <th>Лиды</th>
                          <th>CPL</th>
                          <th>CTR</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {platformAccounts.map((account) => {
                          const metric = accountRows.find((row) => row.account_id === account.id);
                          const status = accountStatus(account);
                          const cpl = metric && metric.conversions > 0 ? metric.spend / metric.conversions : null;
                          const selected = selectedAccountId === account.id;
                          return (
                            <tr key={account.id}>
                              <td>{account.name || account.external_account_id}</td>
                              <td><span className={`badge ${status.tone}`}>{status.label}</span></td>
                              <td>{metric ? fmtMoney(metric.spend, currency) : "Нет данных"}</td>
                              <td>{metric ? fmtNum(metric.conversions) : "Нет данных"}</td>
                              <td>{cpl == null ? "Нет данных" : fmtMoney(cpl, currency)}</td>
                              <td>{metric ? fmtRate(metric.ctr) : "Нет данных"}</td>
                              <td>
                                <button
                                  className={selected ? "primary-btn" : "ghost-btn"}
                                  onClick={() => setSelectedAccountId(selected ? "" : account.id)}
                                >
                                  {selected ? "Выбран" : "Разобрать"}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                        {!platformAccounts.length ? (
                          <tr>
                            <td colSpan={7} className="muted-note">
                              У клиента нет подключённых аккаунтов {platformLabel(activePlatform)}.
                            </td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                </article>

                <aside className={`blueprint-note ${advertisingInsight?.priority === "high" ? "bad" : ""}`.trim()}>
                  <h3>Почему результат изменился</h3>
                  {advertisingInsight ? (
                    <>
                      <p><strong>{advertisingInsightCopy?.title}</strong></p>
                      <p>{advertisingInsightCopy?.reason}</p>
                      <p>
                        <strong>{soloClientMode ? "Рекомендация:" : "Рекомендация агентству:"}</strong>
                        <br />
                        {actionLabel(advertisingInsight.action)}
                      </p>
                      <span className={`badge ${priorityMeta(advertisingInsight.priority).tone}`}>
                        {priorityMeta(advertisingInsight.priority).label}
                      </span>
                    </>
                  ) : (
                    <p>Для выбранной площадки или аккаунта значимое объяснение не сформировано.</p>
                  )}
                  <p className="panel-subtitle">
                    Кампании пока не показаны: подключение передаёт сводку по площадкам и аккаунтам. Детализация до
                    кампаний появится после расширения интеграции.
                  </p>
                </aside>
              </section>
            </>
          ) : null}

          {activeTab === "leads" ? (
            <>
              <section className="kpi-grid">
                <MetricCard
                  title="Получено"
                  value={fmtNum(totalLeads)}
                  note="Конверсии рекламных площадок"
                />
                <MetricCard
                  title="Стоимость"
                  value={totalCpl == null ? "Нет данных" : fmtMoney(totalCpl, currency)}
                  note="Расход ÷ полученные конверсии"
                />
                <MetricCard
                  title="Источники"
                  value={fmtNum(conversionsByPlatform.length)}
                  note="Площадки с конверсиями за период"
                />
                <MetricCard
                  title="Передано в CRM"
                  value="Нет данных"
                  note="Данные о передаче в CRM ещё не подключены"
                />
              </section>

              <article className="panel" style={{ marginTop: 16 }}>
                <h3>Лиды по рекламным источникам</h3>
                <div className="panel-subtitle">
                  В этом разделе конверсия рекламной площадки считается полученным лидом
                </div>
                <div className="budgets-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Источник</th>
                        <th>Расход</th>
                        <th>Получено</th>
                        <th>Стоимость</th>
                        <th>Передано в CRM</th>
                      </tr>
                    </thead>
                    <tbody>
                      {conversionsByPlatform.map((row) => (
                        <tr key={row.platform}>
                          <td>{platformLabel(row.platform)}</td>
                          <td>{fmtMoney(row.spend, currency)}</td>
                          <td>{fmtNum(row.conversions)}</td>
                          <td>{row.cpl == null ? "Нет данных" : fmtMoney(row.cpl, currency)}</td>
                          <td>Нет данных</td>
                        </tr>
                      ))}
                      {!conversionsByPlatform.length ? (
                        <tr><td colSpan={5} className="muted-note">За выбранный период конверсий нет.</td></tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </article>

              <section className="role-dashboard-grid">
                <div className="blueprint-note">
                  <strong>Как сейчас считается лид</strong>
                  <p>
                    Используется поле «конверсии» из Google Ads, Meta Ads и TikTok Ads. Сейчас мы не можем
                    подтвердить, что повторные или невалидные заявки исключены.
                  </p>
                </div>
                <div className="blueprint-note">
                  <strong>Граница платформы</strong>
                  <p>
                    Мы показываем рекламный результат. Передача лида и дальнейшая работа со сделкой должны
                    подтверждаться данными CRM.
                  </p>
                </div>
              </section>
            </>
          ) : null}

          {activeTab === "changes" ? (
            <section className="role-dashboard-grid" style={{ marginTop: 16 }}>
              <article className="panel">
                <h3>Значимые изменения</h3>
                <div className="panel-subtitle">Только фактические сигналы, сформированные для выбранного периода</div>
                <div className="budgets-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Объект</th>
                        <th>Изменение</th>
                        <th>Причина</th>
                        <th>Приоритет</th>
                        <th>Рекомендация</th>
                      </tr>
                    </thead>
                    <tbody>
                      {actionableInsights.map((insight, index) => {
                        const priority = priorityMeta(insight.priority);
                        const copy = insightCopy(insight, currency);
                        return (
                          <tr key={`${insight.scope}-${insight.scope_id}-${index}`}>
                            <td>{describeScope(insight.scope, insight.scope_id)}</td>
                            <td>{copy.title}</td>
                            <td>{copy.reason}</td>
                            <td><span className={`badge ${priority.tone}`}>{priority.label}</span></td>
                            <td>{actionLabel(insight.action)}</td>
                          </tr>
                        );
                      })}
                      {!actionableInsights.length ? (
                        <tr><td colSpan={5} className="muted-note">{dataState === "current" ? "Значимых изменений не найдено." : "Недостаточно свежих данных для оценки изменений."}</td></tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </article>

              <aside className={`blueprint-note ${headlineChange?.priority === "high" ? "bad" : ""}`.trim()}>
                <h3>Текущее отклонение</h3>
                {dataState === "current" && headlineChange ? (
                  <>
                    <p><strong>{headlineChangeCopy?.title}</strong></p>
                    <p>{headlineChangeCopy?.reason}</p>
                    <p>
                      <strong>Что рекомендуется:</strong>
                      <br />
                      {actionLabel(headlineChange.action)}
                    </p>
                  </>
                ) : dataState === "current" ? (
                  <p>Отклонений, требующих объяснения, сейчас нет.</p>
                ) : (
                  <p>{dataMeta.description}</p>
                )}
              </aside>
            </section>
          ) : null}

          {activeTab === "plan" ? (
            <>
              <section className="kpi-grid">
                <MetricCard title="В работе" value={fmtNum(planCounts.inProgress)} note="Поставлены агентством" />
                <MetricCard title="Выполнено" value={fmtNum(planCounts.completed)} note="По данным журнала действий" tone="good" />
                <MetricCard title="С ошибкой" value={fmtNum(planCounts.failed)} note="Требуют повторной проверки" tone={planCounts.failed ? "bad" : ""} />
                <MetricCard title="Всего" value={fmtNum(actions.length)} note="Действия в доступной истории" />
              </section>

              <article className="panel" style={{ marginTop: 16 }}>
                <h3>План действий агентства</h3>
                <div className="panel-subtitle">Клиент видит ход работ, но не может изменять рекламные настройки</div>
                <div className="budgets-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Действие</th>
                        <th>Объект</th>
                        <th>Состояние</th>
                        <th>Создано</th>
                      </tr>
                    </thead>
                    <tbody>
                      {actions.map((action) => {
                        const status = actionStatus(action.status);
                        return (
                          <tr key={action.id}>
                            <td>{actionTitle(action)}</td>
                            <td>{describeScope(action.scope, action.scope_id)}</td>
                            <td><span className={`badge ${status.tone}`}>{status.label}</span></td>
                            <td>{fmtDate(action.created_at)}</td>
                          </tr>
                        );
                      })}
                      {!actions.length ? (
                        <tr><td colSpan={4} className="muted-note">Агентство ещё не создало действий.</td></tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </article>

              <div className="blueprint-note" style={{ marginTop: 16 }}>
                <strong>Каких данных пока нет</strong>
                <p>
                  Сейчас доступны тип действия, объект, состояние и дата. Ответственный, срок и ожидаемый эффект
                  появятся после расширения журнала работ.
                </p>
              </div>
            </>
          ) : null}

          {activeTab === "reports" ? (
            <>
              <div className="blueprint-note" style={{ marginTop: 16 }}>
                <strong>Отчёт за {periodLabel}</strong>
                <p>
                  {dataState === "current"
                    ? "Этот отчёт собран из свежих подтверждённых рекламных данных."
                    : `Отчёт сформирован при неполной готовности данных: ${dataMeta.description}`}
                  {" "}Сохранённые версии и расписание отправки пока недоступны; кнопка «Скачать PDF» сохраняет текущий срез.
                </p>
              </div>

              <section className="kpi-grid">
                <MetricCard title="Расход" value={fmtMoney(totalSpend, currency)} note={pace.label} tone={pace.tone} />
                <MetricCard title="Полученные лиды" value={fmtNum(totalLeads)} note="Конверсии рекламных площадок" />
                <MetricCard
                  title="Стоимость лида"
                  value={totalCpl == null ? "Нет данных" : fmtMoney(totalCpl, currency)}
                  note="Расход ÷ конверсии"
                />
                <MetricCard title="Аккаунты" value={fmtNum(accounts.length)} note="Подключены к выбранному клиенту" />
              </section>

              <section className="role-dashboard-grid">
                <article className="panel">
                  <h3>Результаты по рекламным аккаунтам</h3>
                  <div className="budgets-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Аккаунт</th>
                          <th>Площадка</th>
                          <th>Расход</th>
                          <th>Лиды</th>
                          <th>CPL</th>
                          <th>CTR</th>
                        </tr>
                      </thead>
                      <tbody>
                        {accountRows.map((account) => {
                          const cpl = account.conversions > 0 ? account.spend / account.conversions : null;
                          return (
                            <tr key={account.account_id}>
                              <td>{account.name}</td>
                              <td>{platformLabel(account.platform)}</td>
                              <td>{fmtMoney(account.spend, currency)}</td>
                              <td>{fmtNum(account.conversions)}</td>
                              <td>{cpl == null ? "Нет данных" : fmtMoney(cpl, currency)}</td>
                              <td>{fmtRate(account.ctr)}</td>
                            </tr>
                          );
                        })}
                        {!accountRows.length ? (
                          <tr><td colSpan={6} className="muted-note">Детализация по аккаунтам недоступна.</td></tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                </article>

                <aside className="blueprint-note">
                  <h3>Вывод и следующие шаги</h3>
                  <p>{summaryText}</p>
                  {dataState === "current" && headlineInsight ? (
                    <p>
                      <strong>{headlineInsightCopy?.title}</strong>
                      <br />
                      {headlineInsightCopy?.reason}
                    </p>
                  ) : null}
                  {headlineAction ? (
                    <p>
                      <strong>В работе:</strong>
                      <br />
                      {actionTitle(headlineAction)}
                    </p>
                  ) : null}
                </aside>
              </section>
            </>
          ) : null}

          {activeTab === "billing" ? (
            <>
              <section className="kpi-grid">
                <MetricCard
                  title="Бюджет"
                  value={
                    overview?.budget_summary?.budget == null
                      ? "Не задан"
                      : fmtMoney(overview?.budget_summary?.budget, currency)
                  }
                  note="Доступный бюджет выбранного периода"
                />
                <MetricCard title="Расход" value={fmtMoney(overview?.budget_summary?.spend || 0, currency)} note={pace.label} tone={pace.tone} />
                <MetricCard
                  title="Остаток"
                  value={
                    overview?.budget_summary?.remaining == null
                      ? "Нет данных"
                      : fmtMoney(overview?.budget_summary?.remaining, currency)
                  }
                  note="Бюджет минус фактический расход"
                />
                <MetricCard
                  title="Прогноз"
                  value={
                    overview?.budget_summary?.forecast_spend == null
                      ? "Нет данных"
                      : fmtMoney(overview?.budget_summary?.forecast_spend, currency)
                  }
                  note="Прогноз расхода к концу периода"
                />
              </section>

              <article className="panel" style={{ marginTop: 16 }}>
                <h3>Бюджетные лимиты</h3>
                <div className="budgets-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Уровень</th>
                        <th>Аккаунт</th>
                        <th>Сумма</th>
                        <th>Состояние</th>
                        <th>Начало</th>
                        <th>Окончание</th>
                      </tr>
                    </thead>
                    <tbody>
                      {budgets.map((budget) => (
                        <tr key={budget.id || `${budget.client_id}-${budget.account_id || "client"}`}>
                          <td>{budget.scope === "client" ? "Клиент" : "Рекламный аккаунт"}</td>
                          <td>
                            {budget.account_id
                              ? accounts.find((account) => account.id === budget.account_id)?.name || "Аккаунт"
                              : selectedClient?.name || "Весь клиент"}
                          </td>
                          <td>{fmtMoney(Number(budget.amount || 0), budget.currency || currency)}</td>
                          <td>{budget.status === "archived" ? "Архив" : "Активен"}</td>
                          <td>{fmtShortDate(budget.start_date)}</td>
                          <td>{fmtShortDate(budget.end_date)}</td>
                        </tr>
                      ))}
                      {!budgets.length ? (
                        <tr><td colSpan={6} className="muted-note">Бюджетные лимиты ещё не заданы.</td></tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </article>

              <ProviderBudgetControl
                apiBase={session.apiBase}
                token={session.token}
                clients={clients}
                accounts={accounts}
                role={soloClientMode ? "solo_client" : "client"}
                initialClientId={selectedClientId}
                compact
              />
            </>
          ) : null}
        </main>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
