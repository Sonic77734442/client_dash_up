"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { StateMessage } from "../../components/common/StateMessage";
import { useSession } from "../../hooks/useSession";
import { fetchJson } from "../../lib/api";
import { normalizeIntegrationsOverviewPayload } from "../../lib/analyticsPayload";
import {
  hasOptionalStringFields,
  hasStringFields,
  normalizeListPayload,
} from "../../lib/listPayload";
import {
  AgencyOut,
  AlertOut,
  AuthUser,
  ClientOut,
  IntegrationsOverview,
} from "../../lib/types";

function isAuthUserItem(value: unknown): value is AuthUser {
  return (
    hasStringFields(value, ["id", "name", "role", "status"]) &&
    hasOptionalStringFields(value, ["email"])
  );
}

function isAgencyItem(value: unknown): value is AgencyOut {
  return hasStringFields(value, ["id", "name", "status"]);
}

function isClientItem(value: unknown): value is ClientOut {
  return hasStringFields(value, ["id", "name"]);
}

function isAlertItem(value: unknown): value is AlertOut {
  return hasStringFields(value, ["id", "code", "severity", "title"]);
}

export default function PlatformDecisionCenterPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [agencies, setAgencies] = useState<AgencyOut[]>([]);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationsOverview | null>(null);
  const [warning, setWarning] = useState("");
  const [loading, setLoading] = useState(true);

  const req = useCallback(
    <T,>(path: string) => fetchJson<T>(session.apiBase, path, session.token),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [userRows, agencyRows, clientRows, alertRows, integrationRows] = await Promise.all([
        req<unknown>("/auth/internal/users"),
        req<unknown>("/platform/agencies?status=all"),
        req<unknown>("/clients?status=all"),
        req<unknown>("/alerts?status=open&limit=100"),
        req<unknown>("/integrations/overview"),
      ]);
      const nextUsers = normalizeListPayload(userRows, isAuthUserItem, "пользователей");
      const nextAgencies = normalizeListPayload(agencyRows, isAgencyItem, "агентств");
      const nextClients = normalizeListPayload(clientRows, isClientItem, "клиентов");
      const nextAlerts = normalizeListPayload(alertRows, isAlertItem, "инцидентов");
      const nextIntegrations = normalizeIntegrationsOverviewPayload(integrationRows);

      setUsers(nextUsers);
      setAgencies(nextAgencies);
      setClients(nextClients);
      setAlerts(nextAlerts);
      setIntegrations(nextIntegrations);
      setWarning("");
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить центр решений");
    } finally {
      setLoading(false);
    }
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadData();
  }, [ready, loadData]);

  const decisions = useMemo(() => {
    const items: Array<{
      key: string;
      level: "critical" | "warning" | "info";
      title: string;
      detail: string;
      href: string;
      action: string;
    }> = [];

    for (const alert of alerts
      .filter((item) => item.severity === "critical" || item.severity === "high")
      .slice(0, 5)) {
      items.push({
        key: `alert-${alert.id}`,
        level: alert.severity === "critical" ? "critical" : "warning",
        title: alert.title || alert.code,
        detail: `${alert.provider || "Платформа"} · повторений: ${alert.occurrences}`,
        href: "/platform/alerts",
        action: "Разобрать",
      });
    }

    const inactiveUsers = users.filter((item) => item.status === "inactive").slice(0, 3);
    for (const user of inactiveUsers) {
      items.push({
        key: `user-${user.id}`,
        level: "info",
        title: user.name || user.email || "Пользователь",
        detail: "Учётная запись не активна — требуется решение администратора",
        href: "/platform/users",
        action: "Проверить",
      });
    }

    const suspended = agencies.filter((item) => item.status === "suspended").slice(0, 3);
    for (const agency of suspended) {
      items.push({
        key: `agency-${agency.id}`,
        level: "warning",
        title: agency.name,
        detail: "Агентство приостановлено",
        href: "/platform/agencies",
        action: "Открыть",
      });
    }

    return items.slice(0, 8);
  }, [alerts, users, agencies]);

  const criticalAlerts = alerts.filter((item) => item.severity === "critical").length;
  const inactiveUsers = users.filter((item) => item.status === "inactive").length;
  const suspendedAgencies = agencies.filter((item) => item.status === "suspended").length;
  const connectionProblems =
    Number(integrations?.summary?.critical_issues || 0) +
    Number(integrations?.summary?.warning_connections || 0);

  return (
    <div className="app-shell">
      <AppSidebar active="platform_admin" />
      <main className="content">
        <header className="topbar role-page-topbar">
          <div className="topbar-left">
            <AppTopTabs active="platform_admin" />
            <div className="topbar-title">Центр решений администратора</div>
            <div className="panel-subtitle">Только то, что требует решения сегодня</div>
          </div>
          <div className="session-controls">
            <Link className="primary-btn" href="/?admin_metrics=1">Открыть метрики</Link>
            <button className="ghost-btn" onClick={() => void loadData()} disabled={loading}>Обновить</button>
          </div>
        </header>

        {warning ? <StateMessage title="Не удалось обновить данные" message={warning} /> : null}

        <section className="kpi-grid role-kpi-grid">
          <article className={`kpi-card ${inactiveUsers ? "warn" : "good"}`}>
            <div className="kpi-title">Требуют проверки</div>
            <div className="kpi-value">{inactiveUsers}</div>
            <div className="kpi-meta">Неактивные пользователи</div>
          </article>
          <article className={`kpi-card ${criticalAlerts ? "bad" : "good"}`}>
            <div className="kpi-title">Критические инциденты</div>
            <div className="kpi-value">{criticalAlerts}</div>
            <div className="kpi-meta">Открытые сейчас</div>
          </article>
          <article className={`kpi-card ${connectionProblems ? "warn" : "good"}`}>
            <div className="kpi-title">Проблемы данных</div>
            <div className="kpi-value">{connectionProblems}</div>
            <div className="kpi-meta">Подключения и синхронизации</div>
          </article>
          <article className={`kpi-card ${suspendedAgencies ? "warn" : "good"}`}>
            <div className="kpi-title">Агентства</div>
            <div className="kpi-value">{agencies.length}</div>
            <div className="kpi-meta">{suspendedAgencies ? `${suspendedAgencies} приостановлено` : "Все активны"}</div>
          </article>
        </section>

        <section className="role-dashboard-grid">
          <article className="panel">
            <div className="panel-head">
              <div>
                <h3>Очередь решений</h3>
                <div className="panel-subtitle">Инциденты и доступы собраны в одном месте</div>
              </div>
            </div>
            <div className="decision-list">
              {decisions.map((item) => (
                <div className="decision-row" key={item.key}>
                  <span className={`decision-dot ${item.level}`} aria-hidden="true" />
                  <div>
                    <div className="decision-title">{item.title}</div>
                    <div className="activity-meta">{item.detail}</div>
                  </div>
                  <Link className="ghost-btn" href={item.href}>{item.action}</Link>
                </div>
              ))}
              {!decisions.length && !loading ? (
                <StateMessage title="Всё спокойно" message="Новых критических решений нет." />
              ) : null}
            </div>
          </article>

          <aside className="side-stack">
            <article className="panel">
              <h3>Структура платформы</h3>
              <div className="decision-list compact">
                <Link className="decision-row decision-link" href="/platform/agencies">
                  <span>Агентства</span><strong>{agencies.length}</strong>
                </Link>
                <Link className="decision-row decision-link" href="/clients">
                  <span>Клиенты</span><strong>{clients.length}</strong>
                </Link>
                <Link className="decision-row decision-link" href="/platform/users">
                  <span>Пользователи</span><strong>{users.length}</strong>
                </Link>
              </div>
            </article>
            <article className="panel">
              <h3>Состояние платформы</h3>
              <div className="activity-item">
                <div className="activity-title">Подключения</div>
                <div className="activity-meta">
                  {integrations
                    ? `${Number(integrations.summary?.healthy_connections || 0)} работают · ${connectionProblems} требуют внимания`
                    : "Загрузка…"}
                </div>
                <Link className="ghost-btn activity-action" href="/integrations">Открыть подключения</Link>
              </div>
              <div className="activity-item">
                <div className="activity-title">Журнал действий</div>
                <div className="activity-meta">Все чувствительные операции администратора</div>
                <Link className="ghost-btn activity-action" href="/platform/audit">Открыть журнал</Link>
              </div>
            </article>
          </aside>
        </section>
      </main>
    </div>
  );
}
