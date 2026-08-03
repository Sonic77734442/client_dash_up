"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { useSession } from "../../../hooks/useSession";
import { fetchJson } from "../../../lib/api";
import { normalizeProviderConfigs, type ProviderConfig } from "../../../lib/providerConfigs";
import { type IntegrationsOverview } from "../../../lib/types";

export default function PlatformSettingsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationsOverview | null>(null);
  const [warning, setWarning] = useState("");

  const req = useCallback(
    <T,>(path: string) => fetchJson<T>(session.apiBase, path, session.token),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    try {
      const [providerRows, integrationRows] = await Promise.all([
        req<unknown>("/auth/provider-configs"),
        req<IntegrationsOverview>("/integrations/overview"),
      ]);
      setProviders(normalizeProviderConfigs(providerRows));
      setIntegrations(integrationRows);
      setWarning("");
    } catch (error) {
      setProviders([]);
      setIntegrations(null);
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить настройки");
    }
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadData();
  }, [ready, loadData]);

  return (
    <div className="app-shell">
      <AppSidebar active="platform_admin" />
      <main className="content">
        <header className="topbar role-page-topbar">
          <div className="topbar-left">
            <AppTopTabs active="platform_admin" />
            <div className="topbar-title">Настройки платформы</div>
            <div className="panel-subtitle">Редкие системные настройки отделены от ежедневной работы</div>
          </div>
          <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
        </header>

        <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

        <section className="settings-grid">
          <article className="panel">
            <div className="panel-head">
              <div>
                <h3>OAuth-провайдеры</h3>
                <div className="panel-subtitle">Секреты не отображаются — только состояние и redirect URI</div>
              </div>
              <Link className="ghost-btn" href="/integrations">Подключения</Link>
            </div>
            <div className="decision-list">
              {providers.map((provider) => (
                <div className="decision-row settings-row" key={provider.provider}>
                  <span className={`decision-dot ${provider.enabled === false ? "warning" : "info"}`} />
                  <div>
                    <div className="decision-title">{provider.provider}</div>
                    <div className="activity-meta">{provider.redirect_uri || "Redirect URI не указан"}</div>
                  </div>
                  <span className="badge">{provider.enabled === false ? "Выключен" : "Настроен"}</span>
                </div>
              ))}
              {!providers.length ? <div className="muted-note">Конфигурации провайдеров не найдены.</div> : null}
            </div>
          </article>

          <article className="panel">
            <div className="panel-head">
              <div>
                <h3>Здоровье данных</h3>
                <div className="panel-subtitle">Синхронизация и доступность источников</div>
              </div>
              <Link className="ghost-btn" href="/sync-monitor">Диагностика</Link>
            </div>
            <div className="detail-grid">
              <div className="detail-item"><div className="detail-k">Работают</div><div className="detail-v">{integrations?.summary?.healthy_connections ?? "—"}</div></div>
              <div className="detail-item"><div className="detail-k">Предупреждения</div><div className="detail-v">{integrations?.summary?.warning_connections ?? "—"}</div></div>
              <div className="detail-item"><div className="detail-k">Критические</div><div className="detail-v">{integrations?.summary?.critical_issues ?? "—"}</div></div>
              <div className="detail-item"><div className="detail-k">Узлы</div><div className="detail-v">{integrations?.summary?.active_nodes ?? "—"}</div></div>
            </div>
          </article>

          <article className="panel">
            <h3>Тарифы и лимиты</h3>
            <p className="muted">Тариф агентства и разрешение приглашать клиентов управляются в карточке агентства.</p>
            <Link className="ghost-btn" href="/platform/agencies">Открыть агентства</Link>
          </article>

          <article className="panel">
            <h3>Безопасность и аудит</h3>
            <p className="muted">Роли, статусы пользователей, назначения клиентов и история чувствительных действий.</p>
            <div className="alert-actions">
              <Link className="ghost-btn" href="/platform/users">Пользователи</Link>
              <Link className="ghost-btn" href="/platform/access">Карта доступов</Link>
              <Link className="ghost-btn" href="/platform/audit">Журнал</Link>
            </div>
          </article>
        </section>
      </main>
    </div>
  );
}
