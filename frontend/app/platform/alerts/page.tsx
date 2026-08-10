"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson } from "../../../lib/api";
import { AlertOut, AuthMeResponse } from "../../../lib/types";

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function badgeClass(v: string) {
  if (v === "critical" || v === "high") return "bad";
  if (v === "medium") return "warn";
  return "good";
}

const severityLabels: Record<AlertOut["severity"], string> = {
  critical: "Критично",
  high: "Высокая",
  medium: "Средняя",
  low: "Низкая",
};

const statusLabels: Record<AlertOut["status"], string> = {
  open: "Открыт",
  acked: "Принят в работу",
  resolved: "Решён",
};

const alertMessages: Record<string, string> = {
  "account.blocked_or_disabled": "Рекламный аккаунт заблокирован, отключён или недоступен. Проверьте его состояние на стороне платформы.",
  "provider.auth_failed": "Недостаточно прав для обновления данных. Переподключите платформу и подтвердите доступ к рекламному аккаунту.",
  "provider.unavailable": "Рекламная платформа временно недоступна. Повторите обновление позже.",
  "discovery.provider_failed": "Не удалось получить список рекламных аккаунтов. Проверьте подключение и повторите поиск.",
  "discovery.account_blocked_or_disabled": "При поиске найден заблокированный или отключённый рекламный аккаунт.",
  "discovery.auth_failed": "Не удалось получить список аккаунтов из-за недостаточных прав. Переподключите платформу.",
};

function alertMessage(alert: AlertOut) {
  return alertMessages[alert.code] || alert.message;
}

export default function PlatformAlertsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [status, setStatus] = useState<"open" | "acked" | "resolved" | "all">("open");
  const [severity, setSeverity] = useState<"" | "critical" | "high" | "medium" | "low">("");
  const [provider, setProvider] = useState("");
  const [alerts, setAlerts] = useState<AlertOut[]>([]);

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const me = await req<AuthMeResponse>("/auth/me");
    if (me.user.role !== "admin") {
      throw new Error("Требуются права администратора");
    }
    const q = new URLSearchParams({ status, limit: "200" });
    if (severity) q.set("severity", severity);
    if (provider.trim()) q.set("provider", provider.trim().toLowerCase());
    const rows = await req<AlertOut[]>(`/alerts?${q.toString()}`);
    setAlerts(rows || []);
  }, [req, provider, severity, status]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Не удалось загрузить инциденты"));
  }, [ready, loadData]);

  async function ackAlert(alertId: string) {
    try {
      await req<AlertOut>(`/alerts/${alertId}/ack`, { method: "POST" });
      await loadData();
      push("Инцидент принят в работу", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось принять инцидент в работу", "error");
    }
  }

  async function resolveAlert(alertId: string) {
    try {
      await req<AlertOut>(`/alerts/${alertId}/resolve`, { method: "POST" });
      await loadData();
      push("Инцидент отмечен решённым", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось закрыть инцидент", "error");
    }
  }

  const summary = useMemo(() => {
    return {
      total: alerts.length,
      critical: alerts.filter((x) => x.severity === "critical").length,
      high: alerts.filter((x) => x.severity === "high").length,
      open: alerts.filter((x) => x.status === "open").length,
    };
  }, [alerts]);

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="platform_admin" subtitle="Управление платформой" />
        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="platform_admin" />
              <div className="topbar-title">Инциденты платформы</div>
            </div>
            <div className="session-controls">
              <a className="ghost-btn" href="/platform/users">Пользователи</a>
              <a className="ghost-btn" href="/platform/agencies">Агентства</a>
              {tokenLoginEnabled ? (
                <>
                  <input value={session.apiBase} onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))} placeholder="API base" />
                  <input type="password" value={session.token} onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))} placeholder="Session token" />
                  <button
                    className="ghost-btn"
                    onClick={async () => {
                      const next = { apiBase: session.apiBase.trim().replace(/\/$/, "") || defaultApiBase, token: session.token.trim() };
                      persist(next);
                      setSession(next);
                      await loadData();
                      push("Сеанс сохранён", "success");
                    }}
                  >
                    Сохранить
                  </button>
                </>
              ) : null}
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="kpi-grid" style={{ marginTop: 12 }}>
            <article className="kpi-card"><div className="kpi-title">Всего</div><div className="kpi-value">{summary.total}</div></article>
            <article className="kpi-card bad"><div className="kpi-title">Критические</div><div className="kpi-value">{summary.critical}</div></article>
            <article className="kpi-card bad"><div className="kpi-title">Высокая важность</div><div className="kpi-value">{summary.high}</div></article>
            <article className="kpi-card warn"><div className="kpi-title">Открытые</div><div className="kpi-value">{summary.open}</div></article>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="chip-row" style={{ marginTop: 0 }}>
              <label>
                Статус
                <select value={status} onChange={(e) => setStatus(e.target.value as "open" | "acked" | "resolved" | "all")}>
                  <option value="open">Открытые</option>
                  <option value="acked">В работе</option>
                  <option value="resolved">Решённые</option>
                  <option value="all">Все</option>
                </select>
              </label>
              <label>
                Важность
                <select value={severity} onChange={(e) => setSeverity(e.target.value as "" | "critical" | "high" | "medium" | "low")}>
                  <option value="">Все</option>
                  <option value="critical">Критическая</option>
                  <option value="high">Высокая</option>
                  <option value="medium">Средняя</option>
                  <option value="low">Низкая</option>
                </select>
              </label>
              <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="Платформа: google, meta или tiktok" />
              <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
            </div>

            <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
              <table className="budgets-table">
                <thead>
                  <tr>
                    <th>Важность</th>
                    <th>Статус</th>
                    <th>Код</th>
                    <th>Платформа</th>
                    <th>Причина и следующее действие</th>
                    <th>Повторения</th>
                    <th>Последнее событие</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((a) => (
                    <tr key={a.id}>
                      <td><span className={`badge ${badgeClass(a.severity)}`}>{severityLabels[a.severity]}</span></td>
                      <td>{statusLabels[a.status]}</td>
                      <td>{a.code}</td>
                      <td>{a.provider || "--"}</td>
                      <td>{alertMessage(a)}</td>
                      <td>{a.occurrences}</td>
                      <td>{fmtDate(a.last_seen_at)}</td>
                      <td>
                        <button className="ghost-btn" onClick={() => void ackAlert(a.id)} disabled={a.status !== "open"}>
                          В работу
                        </button>
                        <button className="ghost-btn" onClick={() => void resolveAlert(a.id)} disabled={a.status === "resolved"}>
                          Закрыть
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!alerts.length ? (
                    <tr>
                      <td colSpan={8} className="muted-note">Инцидентов по выбранным фильтрам нет.</td>
                    </tr>
                  ) : null}
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
