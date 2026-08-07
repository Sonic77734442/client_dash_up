"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { agencySelectionRequiredMessage, useAgencyContext } from "../../../hooks/useAgencyContext";
import { useOperationalActions } from "../../../hooks/useOperationalActions";
import { useSession } from "../../../hooks/useSession";
import { useScopeRequestGuard } from "../../../hooks/useScopeRequestGuard";
import { useToast } from "../../../hooks/useToast";
import { fetchJson, getQuery } from "../../../lib/api";
import { accountDataFreshness, dataFreshnessMeta, overviewDataFreshness } from "../../../lib/dataFreshness";
import { AdAccount, Budget, ClientOut, OperationalAction, Overview } from "../../../lib/types";

type ActionKind = "cap" | "review" | "scale";
type ActionScope = "client" | "account";
type Tone = "good" | "warn" | "bad" | "";

function fmtNum(value: number | null | undefined) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Number(value || 0));
}

function fmtMoney(value: number | null | undefined, currency?: string | null) {
  const amount = Number(value || 0);
  if (!currency) return fmtNum(amount);
  const normalized = String(currency).toUpperCase();
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency: normalized,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${fmtNum(amount)} ${normalized}`;
  }
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
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

function dateRange(periodDays: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (periodDays - 1));
  const fmt = (date: Date) => date.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

function platformLabel(platform: string) {
  const labels: Record<string, string> = {
    google: "Google Ads",
    meta: "Meta Ads",
    tiktok: "TikTok Ads",
  };
  return labels[String(platform || "").toLowerCase()] || platform || "Неизвестная площадка";
}

function actionLabel(action: string) {
  const labels: Record<string, string> = {
    cap: "Ограничить расход",
    review: "Проверить стратегию",
    scale: "Увеличить бюджет",
    pause: "Приостановить",
  };
  return labels[String(action || "").toLowerCase()] || action || "Проверить";
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

function paceMeta(status?: string | null) {
  const normalized = String(status || "on_track").toLowerCase();
  if (normalized.includes("over") || normalized.includes("critical")) {
    return {
      label: "Выше планового темпа",
      description: "При текущем темпе есть риск превысить рекламный бюджет.",
      tone: "bad" as Tone,
    };
  }
  if (normalized.includes("under") || normalized.includes("slow")) {
    return {
      label: "Ниже планового темпа",
      description: "Бюджет расходуется медленнее ожидаемого темпа.",
      tone: "warn" as Tone,
    };
  }
  if (normalized.includes("no_budget")) {
    return {
      label: "План не задан",
      description: "Для выбранного периода нет активного бюджетного плана.",
      tone: "" as Tone,
    };
  }
  return {
    label: "В пределах плана",
    description: "Расход соответствует доступному бюджетному плану.",
    tone: "good" as Tone,
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

export default function ClientDetailsPage({ clientId }: { clientId: string }) {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const agencyContext = useAgencyContext({ apiBase: session.apiBase, token: session.token, loadPortfolio: true });
  const beginScopedRequest = useScopeRequestGuard(
    `${agencyContext.selectedAgencyId || agencyContext.role || "unknown"}:${clientId}`,
  );
  const { toasts, push } = useToast();
  const { executeAction, listActions } = useOperationalActions(session.apiBase, session.token);

  const [periodDays, setPeriodDays] = useState(30);
  const [client, setClient] = useState<ClientOut | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [actions, setActions] = useState<OperationalAction[]>([]);
  const [warning, setWarning] = useState("");
  const [loading, setLoading] = useState(false);
  const [busyAction, setBusyAction] = useState("");

  const request = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    if (!clientId) return;
    const isCurrentRequest = beginScopedRequest();
    if (
      agencyContext.role === "agency"
      && (!agencyContext.portfolioReady || !agencyContext.clientIds.includes(clientId))
    ) {
      setClient(null);
      setOverview(null);
      setAccounts([]);
      setBudgets([]);
      setActions([]);
      setWarning(
        agencyContext.selectionRequired
          ? agencySelectionRequiredMessage()
          : "Этот клиент не входит в выбранное агентство.",
      );
      return;
    }
    setLoading(true);
    try {
      setWarning("");
      const range = dateRange(periodDays);
      const query = getQuery({ client_id: clientId, date_from: range.from, date_to: range.to });
      const [clientPayload, overviewPayload, accountPayload, budgetPayload, actionPayload] = await Promise.all([
        request<ClientOut>(`/clients/${clientId}`),
        request<Overview>(`/insights/overview${query}`),
        request<{ items: AdAccount[] }>(`/ad-accounts${getQuery({ client_id: clientId, status: "active" })}`),
        request<{ items: Budget[] }>(
          `/budgets${getQuery({
            client_id: clientId,
            status: "active",
            date_from: range.from,
            date_to: range.to,
          })}`
        ),
        listActions({ clientId }),
      ]);
      if (!isCurrentRequest()) return;
      setClient(clientPayload);
      setOverview(overviewPayload);
      setAccounts(accountPayload.items || []);
      setBudgets(budgetPayload.items || []);
      setActions(Array.isArray(actionPayload) ? actionPayload : []);
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить карточку клиента.");
    } finally {
      setLoading(false);
    }
  }, [
    agencyContext.clientIds,
    agencyContext.portfolioReady,
    agencyContext.role,
    agencyContext.selectionRequired,
    beginScopedRequest,
    clientId,
    listActions,
    periodDays,
    request,
  ]);

  useEffect(() => {
    if (!ready || !clientId || agencyContext.loading) return;
    void loadData();
  }, [agencyContext.loading, ready, clientId, loadData]);

  useEffect(() => {
    setClient(null);
    setOverview(null);
    setAccounts([]);
    setBudgets([]);
    setActions([]);
    setBusyAction("");
  }, [agencyContext.selectedAgencyId]);

  const clientBudget = useMemo(
    () => budgets.find((budget) => budget.scope === "client") || null,
    [budgets]
  );

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

  const resolvedCurrency =
    client?.default_currency ||
    clientBudget?.currency ||
    budgets.find((budget) => budget.currency)?.currency ||
    accounts.find((account) => account.currency)?.currency ||
    null;

  const totalSpend = Number(overview?.spend_summary?.spend || 0);
  const isReadOnly = client?.status !== "active";
  const totalConversions = Number(overview?.spend_summary?.conversions || 0);
  const averageCpl = totalConversions > 0 ? totalSpend / totalConversions : null;
  const overviewBudget =
    overview?.budget_summary?.budget == null
      ? clientBudget
        ? Number(clientBudget.amount || 0)
        : null
      : Number(overview.budget_summary.budget);
  const dataState = overviewDataFreshness(overview);
  const dataMeta = dataFreshnessMeta(dataState);
  const rawPace = paceMeta(overview?.budget_summary?.pace_status);
  const pace = dataState === "current"
    ? rawPace
    : { label: dataMeta.label, description: dataMeta.description, tone: dataMeta.tone as Tone };
  const usage = overview?.budget_summary?.usage_percent;
  const periodLabel = overview
    ? `${fmtShortDate(overview.range.date_from)} — ${fmtShortDate(overview.range.date_to)}`
    : `${periodDays} дней`;

  const accountName = (accountId?: string | null) =>
    accounts.find((account) => account.id === accountId)?.name || "Рекламный аккаунт";

  const actionTarget = (action: OperationalAction) => {
    if (action.scope === "client") return client?.name || "Клиент";
    if (action.scope === "account") return accountName(action.scope_id);
    return "Рекламный объект";
  };

  const summaryText = dataState !== "current"
    ? dataMeta.description
    : totalSpend
    ? `За ${periodLabel} клиент потратил ${fmtMoney(totalSpend, resolvedCurrency)} и получил ${fmtNum(
        totalConversions
      )} конверсий из рекламных площадок. ${pace.description}`
    : `За ${periodLabel} рекламные площадки не вернули расход и конверсии. Проверьте период и состояние подключений.`;

  async function runQuickAction(scope: ActionScope, action: ActionKind, accountId?: string) {
    if (isReadOnly) {
      push("Неактивный или архивный клиент доступен только для просмотра истории.", "info");
      return;
    }
    const targetAccount = scope === "account" ? accounts.find((account) => account.id === accountId) : null;
    const key = `${scope}:${accountId || clientId}:${action}`;
    setBusyAction(key);
    try {
      const targetName = scope === "client" ? client?.name || "клиент" : targetAccount?.name || "рекламный аккаунт";
      const performance = accountId ? accountPerfMap.get(accountId) : null;
      await executeAction({
        action,
        scope,
        scope_id: scope === "client" ? clientId : accountId || "",
        client_id: clientId,
        account_id: scope === "account" ? accountId : undefined,
        title: `${actionLabel(action)}: ${targetName}`,
        reason: "Создано из карточки клиента агентством",
        metrics:
          scope === "client"
            ? {
                spend: totalSpend,
                conversions: totalConversions,
                budget: overviewBudget,
                pace_status: overview?.budget_summary?.pace_status,
              }
            : {
                spend: performance?.spend || 0,
                clicks: performance?.clicks || 0,
                conversions: performance?.conversions || 0,
                ctr: performance?.ctr || 0,
                cpc: performance?.cpc || 0,
              },
      });
      const actionPayload = await listActions({ clientId });
      setActions(Array.isArray(actionPayload) ? actionPayload : []);
      push(`Действие «${actionLabel(action)}» поставлено в работу`, "success");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Не удалось создать действие.";
      setWarning(message);
      push("Не удалось создать действие", "error");
    } finally {
      setBusyAction("");
    }
  }

  return (
    <div className="app-shell">
      <AppSidebar
        active="clients"
        subtitle={client?.name ? `${client.name} · рабочая область` : "Карточка клиента"}
      />

      <main className="content">
        <header className="topbar role-page-topbar">
          <div className="topbar-left">
            <AppTopTabs
              active="clients"
              contextLabel={client?.name}
              sectionLabel="Карточка клиента"
            />
            <div className="topbar-title">{client?.name || "Карточка клиента"}</div>
            <div className="panel-subtitle">
              Результат по рекламным площадкам, аккаунтам и бюджету · {periodLabel}
            </div>
          </div>
          <div className="session-controls">
            <Link className="ghost-btn" href="/clients">Все клиенты</Link>
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
          <div className="asof">
            {overview ? `${dataMeta.label} · срез на ${fmtShortDate(overview.range.as_of_date)}` : "Данные загружаются"}
          </div>
          <button className="ghost-btn" onClick={() => void loadData()} disabled={loading}>
            {loading ? "Обновляем…" : "Обновить"}
          </button>
        </section>

        <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

        {isReadOnly && client ? (
          <div className="warning" style={{ marginTop: 12 }}>
            Клиент {client.status === "archived" ? "в архиве" : "неактивен"}. История доступна для просмотра, новые действия отключены.
          </div>
        ) : null}

        <div className={`blueprint-note ${pace.tone === "bad" ? "bad" : ""}`.trim()} style={{ marginTop: 16 }}>
          <strong>Главное за период</strong>
          <p>{summaryText}</p>
        </div>

        <section className="kpi-grid">
          <MetricCard
            title="Расход"
            value={fmtMoney(totalSpend, resolvedCurrency)}
            note={usage == null ? "Бюджетный план не задан" : `${usage.toFixed(1).replace(".", ",")}% бюджета`}
            tone={pace.tone}
          />
          <MetricCard
            title="Бюджет"
            value={overviewBudget == null ? "Не задан" : fmtMoney(overviewBudget, clientBudget?.currency || resolvedCurrency)}
            note={clientBudget ? "Клиентский бюджет" : "Расчёт из активных бюджетов"}
          />
          <MetricCard
            title="Полученные лиды"
            value={fmtNum(totalConversions)}
            note="Конверсии рекламных площадок"
          />
          <MetricCard
            title="Средняя стоимость"
            value={averageCpl == null ? "Нет данных" : fmtMoney(averageCpl, resolvedCurrency)}
            note="Расход ÷ конверсии; план CPL не задан"
          />
        </section>

        <section className="role-dashboard-grid">
          <article className="panel">
            <div className="panel-head">
              <div>
                <h3>Рекламные аккаунты клиента</h3>
                <div className="panel-subtitle">
                  Фактические показатели; детализация до кампаний пока не подключена
                </div>
              </div>
              <span className="badge">{accounts.length} аккаунт(а)</span>
            </div>
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
                    <th>Состояние</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((account) => {
                    const performance = accountPerfMap.get(account.id);
                    const cpl =
                      performance && performance.conversions > 0
                        ? performance.spend / performance.conversions
                        : null;
                    const status = accountStatus(account);
                    const accountCurrency = account.currency || resolvedCurrency;
                    return (
                      <tr key={account.id}>
                        <td>
                          <strong>{account.name || account.external_account_id}</strong>
                          <div className="panel-subtitle">{account.external_account_id}</div>
                        </td>
                        <td>{platformLabel(account.platform)}</td>
                        <td>{performance ? fmtMoney(performance.spend, accountCurrency) : "Нет данных"}</td>
                        <td>{performance ? fmtNum(performance.conversions) : "Нет данных"}</td>
                        <td>{cpl == null ? "Нет данных" : fmtMoney(cpl, accountCurrency)}</td>
                        <td>{performance ? fmtRate(performance.ctr) : "Нет данных"}</td>
                        <td>
                          <span className={`badge ${status.tone}`}>{status.label}</span>
                          <div className="panel-subtitle">{fmtDate(account.last_sync_at)}</div>
                        </td>
                        <td>
                          <div className="alert-actions" style={{ marginTop: 0 }}>
                            {(["cap", "review", "scale"] as ActionKind[]).map((action) => {
                              const key = `account:${account.id}:${action}`;
                              return (
                                <button
                                  key={action}
                                  className="mini-btn"
                                  disabled={isReadOnly || Boolean(busyAction) || status.tone !== "good"}
                                  onClick={() => void runQuickAction("account", action, account.id)}
                                  title={status.tone !== "good" ? status.description : `${actionLabel(action)} для ${account.name}`}
                                >
                                  {busyAction === key ? "…" : actionLabel(action)}
                                </button>
                              );
                            })}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {!accounts.length ? (
                    <tr>
                      <td colSpan={8} className="muted-note">К клиенту ещё не привязаны рекламные аккаунты.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </article>

          <aside className="side-stack">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <h3>Бюджет</h3>
                  <div className="panel-subtitle">{pace.label}</div>
                </div>
                <span className={`badge ${pace.tone}`}>{pace.label}</span>
              </div>

              <div className="budgets-money-line">
                <strong>
                  {overviewBudget == null
                    ? "Не задан"
                    : fmtMoney(overviewBudget, clientBudget?.currency || resolvedCurrency)}
                </strong>
                <span>
                  {usage == null ? "—" : `${usage.toFixed(1).replace(".", ",")}%`}
                </span>
              </div>

              {usage != null ? (
                <div className={`usage-bar ${usage > 105 ? "high" : usage > 85 ? "mid" : "low"}`} style={{ marginTop: 12 }}>
                  <div style={{ width: `${Math.min(100, Math.max(0, usage))}%` }}></div>
                </div>
              ) : null}

              <div className="decision-list compact">
                <div className="decision-row">
                  <div>
                    <div className="decision-title">Фактический расход</div>
                    <div className="panel-subtitle">{fmtMoney(totalSpend, resolvedCurrency)}</div>
                  </div>
                  <span></span>
                </div>
                <div className="decision-row">
                  <div>
                    <div className="decision-title">Остаток</div>
                    <div className="panel-subtitle">
                      {overview?.budget_summary?.remaining == null
                        ? "Нет данных"
                        : fmtMoney(overview.budget_summary.remaining, clientBudget?.currency || resolvedCurrency)}
                    </div>
                  </div>
                  <span></span>
                </div>
                <div className="decision-row">
                  <div>
                    <div className="decision-title">Прогноз к концу периода</div>
                    <div className="panel-subtitle">
                      {overview?.budget_summary?.forecast_spend == null
                        ? "Нет данных"
                        : fmtMoney(overview.budget_summary.forecast_spend, clientBudget?.currency || resolvedCurrency)}
                    </div>
                  </div>
                  <span></span>
                </div>
              </div>

              {budgets.length ? (
                <div className="budgets-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Лимит</th>
                        <th>Сумма</th>
                        <th>Период</th>
                      </tr>
                    </thead>
                    <tbody>
                      {budgets.map((budget) => (
                        <tr key={budget.id || `${budget.scope}-${budget.account_id || clientId}`}>
                          <td>
                            {budget.scope === "client"
                              ? "Весь клиент"
                              : accountName(budget.account_id)}
                          </td>
                          <td>{fmtMoney(Number(budget.amount || 0), budget.currency || resolvedCurrency)}</td>
                          <td>{fmtShortDate(budget.start_date)} — {fmtShortDate(budget.end_date)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="blueprint-note" style={{ marginTop: 12 }}>
                  <strong>Бюджет не настроен</strong>
                  <p>Создайте клиентский или аккаунтный лимит в разделе «Бюджеты».</p>
                  <Link className="ghost-btn" href="/budgets">Открыть бюджеты</Link>
                </div>
              )}
            </article>

            <article className="panel">
              <h3>Быстрые действия по клиенту</h3>
              <div className="panel-subtitle">
                Действия создаются в операционном журнале и доступны для дальнейшего контроля
              </div>
              <div className="budgets-detail-actions">
                {(["cap", "review", "scale"] as ActionKind[]).map((action) => {
                  const key = `client:${clientId}:${action}`;
                  return (
                    <button
                      key={action}
                      className={action === "review" ? "primary-btn" : "ghost-btn"}
                      disabled={isReadOnly || Boolean(busyAction)}
                      onClick={() => void runQuickAction("client", action)}
                    >
                      {busyAction === key ? "Создаём…" : actionLabel(action)}
                    </button>
                  );
                })}
              </div>
            </article>
          </aside>
        </section>

        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-head">
            <div>
              <h3>История действий</h3>
              <div className="panel-subtitle">Последние операционные решения по клиенту и его аккаунтам</div>
            </div>
            <span className="badge">{actions.length}</span>
          </div>

          {!actions.length ? (
            <div className="blueprint-note" style={{ marginTop: 12 }}>
              <strong>История пока пуста</strong>
              <p>Первое созданное действие появится здесь.</p>
            </div>
          ) : (
            <div className="side-stack" style={{ marginTop: 10 }}>
              {actions.slice(0, 12).map((action) => {
                const status = actionStatus(action.status);
                return (
                  <div key={action.id} className="action-row timeline-item">
                    <div className="action-row-head">
                      <div className="action-title">
                        {actionLabel(action.action)} · {actionTarget(action)}
                      </div>
                      <span className={`status-pill ${status.tone === "good" ? "applied" : status.tone === "bad" ? "failed" : "queued"}`}>
                        {status.label}
                      </span>
                    </div>
                    {action.title ? <div className="action-meta">{action.title}</div> : null}
                    <div className="action-meta">{fmtDate(action.created_at)}</div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </main>

      <ToastHost toasts={toasts} />
    </div>
  );
}
