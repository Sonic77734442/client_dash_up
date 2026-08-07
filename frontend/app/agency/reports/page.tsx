"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { agencySelectionRequiredMessage, useAgencyContext } from "../../../hooks/useAgencyContext";
import { useSession } from "../../../hooks/useSession";
import { useScopeRequestGuard } from "../../../hooks/useScopeRequestGuard";
import { fetchJson, getQuery } from "../../../lib/api";
import { AgencyOverview, Client } from "../../../lib/types";

function dateRange(days: number) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - (days - 1));
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

function money(value: number) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);
}

export default function AgencyReportsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const agencyContext = useAgencyContext({ apiBase: session.apiBase, token: session.token, loadPortfolio: true });
  const beginScopedRequest = useScopeRequestGuard(agencyContext.selectedAgencyId || agencyContext.role || "unknown");
  const [clients, setClients] = useState<Client[]>([]);
  const [overview, setOverview] = useState<AgencyOverview | null>(null);
  const [periodDays, setPeriodDays] = useState(30);
  const [warning, setWarning] = useState("");

  const req = useCallback(
    <T,>(path: string) => fetchJson<T>(session.apiBase, path, session.token),
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
      const range = dateRange(periodDays);
      const [clientRows, overviewRows] = await Promise.all([
        req<{ items: Client[] }>("/clients?status=active"),
        req<AgencyOverview>(`/agency/overview${getQuery({ date_from: range.from, date_to: range.to })}`),
      ]);
      if (!isCurrentRequest()) return;
      const allowedClientIds = agencyContext.role === "agency" ? new Set(agencyContext.clientIds) : null;
      const visibleClients = (clientRows.items || []).filter(
        (client) => !allowedClientIds || allowedClientIds.has(client.id),
      );
      const visiblePerClient = (overviewRows.per_client || []).filter(
        (row) => !allowedClientIds || allowedClientIds.has(row.client_id),
      );
      setClients(visibleClients);
      setOverview({
        ...overviewRows,
        totals: { ...overviewRows.totals, spend: visiblePerClient.reduce((sum, row) => sum + Number(row.spend || 0), 0) },
        per_client: visiblePerClient,
        per_account: (overviewRows.per_account || []).filter(
          (row) => !allowedClientIds || allowedClientIds.has(row.client_id),
        ),
      });
      setWarning("");
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось подготовить отчёты");
    }
  }, [
    agencyContext.clientIds,
    agencyContext.portfolioError,
    agencyContext.portfolioReady,
    agencyContext.role,
    agencyContext.selectionRequired,
    beginScopedRequest,
    periodDays,
    req,
  ]);

  useEffect(() => {
    if (!ready || agencyContext.loading) return;
    void loadData();
  }, [agencyContext.loading, ready, loadData]);

  useEffect(() => {
    setClients([]);
    setOverview(null);
  }, [agencyContext.selectedAgencyId]);

  const clientNames = useMemo(() => new Map(clients.map((item) => [item.id, item.name])), [clients]);
  const rows = useMemo(
    () => [...(overview?.per_client || [])].sort((a, b) => Number(b.spend || 0) - Number(a.spend || 0)),
    [overview]
  );
  const totalSpend = rows.reduce((sum, item) => sum + Number(item.spend || 0), 0);

  return (
    <div className="app-shell">
      <AppSidebar active="dashboard" />
      <main className="content report-print-area">
        <header className="topbar role-page-topbar">
          <div className="topbar-left">
            <AppTopTabs active="dashboard" />
            <div className="topbar-title">Отчёты</div>
            <div className="panel-subtitle">Проверка результатов клиентов и подготовка отчёта к отправке</div>
          </div>
          <div className="session-controls">
            <select value={periodDays} onChange={(event) => setPeriodDays(Number(event.target.value))}>
              <option value={7}>7 дней</option>
              <option value={30}>30 дней</option>
              <option value={90}>90 дней</option>
            </select>
            <button className="primary-btn" onClick={() => window.print()}>Печать / PDF</button>
          </div>
        </header>

        <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

        <section className="kpi-grid role-kpi-grid">
          <article className="kpi-card"><div className="kpi-title">Клиенты</div><div className="kpi-value">{clients.length}</div></article>
          <article className="kpi-card"><div className="kpi-title">Общий расход</div><div className="kpi-value">{money(totalSpend)}</div></article>
          <article className="kpi-card"><div className="kpi-title">С данными</div><div className="kpi-value">{rows.filter((item) => Number(item.spend || 0) > 0).length}</div></article>
          <article className="kpi-card"><div className="kpi-title">Период</div><div className="kpi-value" style={{ fontSize: 24 }}>{periodDays} дней</div></article>
        </section>

        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-head">
            <div>
              <h3>Отчёты по клиентам</h3>
              <div className="panel-subtitle">Открытие клиента ведёт в рабочий дашборд с платформами и аккаунтами</div>
            </div>
          </div>
          <div className="budgets-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Клиент</th>
                  <th>Расход</th>
                  <th>Доля портфеля</th>
                  <th>Состояние данных</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => {
                  const share = totalSpend > 0 ? (Number(item.spend || 0) / totalSpend) * 100 : 0;
                  return (
                    <tr key={item.client_id}>
                      <td>{clientNames.get(item.client_id) || item.client_id}</td>
                      <td>{money(Number(item.spend || 0))}</td>
                      <td>{share.toFixed(1)}%</td>
                      <td><span className={`badge ${Number(item.spend || 0) > 0 ? "good" : "warn"}`}>{Number(item.spend || 0) > 0 ? "Данные получены" : "Нет данных"}</span></td>
                      <td><Link className="ghost-btn" href={`/client/${item.client_id}`}>Открыть дашборд</Link></td>
                    </tr>
                  );
                })}
                {!rows.length ? <tr><td colSpan={5} className="muted-note">Данных для отчёта пока нет.</td></tr> : null}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
