"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { agencySelectionRequiredMessage, useAgencyContext } from "../../../hooks/useAgencyContext";
import { useSession } from "../../../hooks/useSession";
import { useScopeRequestGuard } from "../../../hooks/useScopeRequestGuard";
import { useToast } from "../../../hooks/useToast";
import { fetchJson, getQuery } from "../../../lib/api";
import { Client, OperationalAction, OperationalInsight } from "../../../lib/types";

type InsightRow = OperationalInsight & { client_id: string; client_name: string };

function dateRange(days: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (days - 1));
  return {
    from: from.toISOString().slice(0, 10),
    to: to.toISOString().slice(0, 10),
  };
}

export default function AgencyActionsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const agencyContext = useAgencyContext({ apiBase: session.apiBase, token: session.token, loadPortfolio: true });
  const agencyScopeKey = agencyContext.selectedAgencyId || agencyContext.role || "unknown";
  const beginScopedRequest = useScopeRequestGuard(agencyScopeKey);
  const beginActionRequest = useScopeRequestGuard(agencyScopeKey);
  const { toasts, push } = useToast();
  const [clients, setClients] = useState<Client[]>([]);
  const [insights, setInsights] = useState<InsightRow[]>([]);
  const [actions, setActions] = useState<OperationalAction[]>([]);
  const [priority, setPriority] = useState<"all" | "high" | "medium" | "low">("all");
  const [warning, setWarning] = useState("");
  const [busyKey, setBusyKey] = useState("");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const isCurrentRequest = beginScopedRequest();
    try {
      if (agencyContext.role === "agency" && !agencyContext.portfolioReady) {
        throw new Error(
          agencyContext.selectionRequired
            ? agencySelectionRequiredMessage()
            : agencyContext.portfolioError || "Не удалось загрузить портфель агентства.",
        );
      }
      const clientRows = await req<{ items: Client[] }>("/clients?status=active");
      const allowedClientIds = agencyContext.role === "agency" ? new Set(agencyContext.clientIds) : null;
      const visibleClients = (clientRows.items || []).filter(
        (client) => !allowedClientIds || allowedClientIds.has(client.id),
      );
      const range = dateRange(30);
      const result = await Promise.all(
        visibleClients.map(async (client) => {
          const query = getQuery({ client_id: client.id, date_from: range.from, date_to: range.to });
          const [insightRows, actionRows] = await Promise.all([
            req<{ items: OperationalInsight[] }>(`/insights/operational${query}`),
            req<OperationalAction[]>(`/insights/operational/actions${getQuery({ client_id: client.id })}`),
          ]);
          return {
            insights: (insightRows.items || []).map((item) => ({
              ...item,
              client_id: client.id,
              client_name: client.name,
            })),
            actions: actionRows || [],
          };
        })
      );
      if (!isCurrentRequest()) return;
      setClients(visibleClients);
      setInsights(result.flatMap((item) => item.insights).sort((a, b) => b.score - a.score));
      setActions(result.flatMap((item) => item.actions).sort((a, b) => b.created_at.localeCompare(a.created_at)));
      setWarning("");
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить отклонения");
    }
  }, [
    agencyContext.clientIds,
    agencyContext.portfolioError,
    agencyContext.portfolioReady,
    agencyContext.role,
    agencyContext.selectionRequired,
    beginScopedRequest,
    req,
  ]);

  useEffect(() => {
    if (!ready || agencyContext.loading) return;
    void loadData();
  }, [agencyContext.loading, ready, loadData]);

  useEffect(() => {
    setClients([]);
    setInsights([]);
    setActions([]);
    setBusyKey("");
  }, [agencyContext.selectedAgencyId]);

  const visibleInsights = useMemo(
    () => insights.filter((item) => priority === "all" || item.priority === priority),
    [insights, priority]
  );

  async function queueAction(item: InsightRow) {
    if (
      agencyContext.role === "agency"
      && (!agencyContext.portfolioReady || !agencyContext.clientIds.includes(item.client_id))
    ) {
      push("Действие недоступно: клиент не входит в выбранное агентство.", "error");
      return;
    }
    const isCurrentRequest = beginActionRequest();
    const key = `${item.client_id}-${item.scope}-${item.scope_id}`;
    setBusyKey(key);
    try {
      await req<OperationalAction>("/insights/operational/actions", {
        method: "POST",
        body: JSON.stringify({
          action: item.action,
          scope: item.scope,
          scope_id: item.scope_id,
          title: item.title,
          reason: item.reason,
          metrics: item.metrics || {},
          client_id: item.client_id,
          account_id: item.scope === "account" ? item.scope_id : null,
        }),
      });
      if (isCurrentRequest()) {
        push("Действие поставлено в очередь", "success");
        await loadData();
      }
    } catch (error) {
      push(error instanceof Error ? error.message : "Не удалось создать действие", "error");
    } finally {
      setBusyKey("");
    }
  }

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="dashboard" />
        <main className="content">
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs active="dashboard" />
              <div className="topbar-title">Отклонения и действия</div>
              <div className="panel-subtitle">Что пошло не по плану, почему и какое действие предлагается</div>
            </div>
            <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="kpi-grid role-kpi-grid">
            <article className="kpi-card bad"><div className="kpi-title">Высокий приоритет</div><div className="kpi-value">{insights.filter((item) => item.priority === "high").length}</div></article>
            <article className="kpi-card warn"><div className="kpi-title">Средний приоритет</div><div className="kpi-value">{insights.filter((item) => item.priority === "medium").length}</div></article>
            <article className="kpi-card"><div className="kpi-title">В очереди</div><div className="kpi-value">{actions.filter((item) => item.status === "queued").length}</div></article>
            <article className="kpi-card good"><div className="kpi-title">Клиенты в контуре</div><div className="kpi-value">{clients.length}</div></article>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div className="chip-row" style={{ marginTop: 0 }}>
                {(["all", "high", "medium", "low"] as const).map((value) => (
                  <button
                    className={`chip-btn ${priority === value ? "active" : ""}`}
                    onClick={() => setPriority(value)}
                    key={value}
                  >
                    {value === "all" ? "Все" : value}
                  </button>
                ))}
              </div>
            </div>
            <div className="budgets-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Клиент</th>
                    <th>Отклонение</th>
                    <th>Причина</th>
                    <th>Приоритет</th>
                    <th>Рекомендация</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {visibleInsights.map((item) => {
                    const key = `${item.client_id}-${item.scope}-${item.scope_id}`;
                    return (
                      <tr key={key}>
                        <td><Link href={`/client/${item.client_id}`}>{item.client_name}</Link></td>
                        <td>{item.title}</td>
                        <td>{item.reason}</td>
                        <td><span className={`badge ${item.priority === "high" ? "bad" : item.priority === "medium" ? "warn" : "good"}`}>{item.priority}</span></td>
                        <td>{item.action.toUpperCase()}</td>
                        <td>
                          <button
                            className="primary-btn"
                            disabled={busyKey === key}
                            onClick={() => void queueAction(item)}
                          >
                            {busyKey === key ? "Ставим…" : "Взять в работу"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {!visibleInsights.length ? <tr><td colSpan={6} className="muted-note">Отклонений в выбранном периоде нет.</td></tr> : null}
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
