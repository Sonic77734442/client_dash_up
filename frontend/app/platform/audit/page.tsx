"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { useSession } from "../../../hooks/useSession";
import { fetchJson } from "../../../lib/api";
import { AuditLogOut } from "../../../lib/types";

function readableEvent(value: string) {
  return value.replaceAll(".", " → ").replaceAll("_", " ");
}

export default function PlatformAuditPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const [items, setItems] = useState<AuditLogOut[]>([]);
  const [eventType, setEventType] = useState("");
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");

  const req = useCallback(
    <T,>(path: string) => fetchJson<T>(session.apiBase, path, session.token),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: "300" });
      if (eventType) query.set("event_type", eventType);
      const rows = await req<AuditLogOut[]>(`/audit/logs?${query.toString()}`);
      setItems(rows || []);
      setWarning("");
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить журнал");
    }
  }, [eventType, req]);

  useEffect(() => {
    if (!ready) return;
    void loadData();
  }, [ready, loadData]);

  const eventTypes = useMemo(
    () => Array.from(new Set(items.map((item) => item.event_type))).sort(),
    [items]
  );
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return items;
    return items.filter((item) =>
      `${item.event_type} ${item.resource_type} ${item.resource_id || ""} ${item.actor_user_id || ""} ${JSON.stringify(item.payload)}`
        .toLowerCase()
        .includes(query)
    );
  }, [items, search]);

  return (
    <div className="app-shell">
      <AppSidebar active="platform_admin" />
      <main className="content">
        <header className="topbar role-page-topbar">
          <div className="topbar-left">
            <AppTopTabs active="platform_admin" />
            <div className="topbar-title">Журнал действий</div>
            <div className="panel-subtitle">Неизменяемая история административных и чувствительных операций</div>
          </div>
          <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
        </header>

        <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

        <section className="panel">
          <div className="access-assign-controls audit-filters">
            <label>
              Событие
              <select value={eventType} onChange={(event) => setEventType(event.target.value)}>
                <option value="">Все события</option>
                {eventTypes.map((item) => <option value={item} key={item}>{readableEvent(item)}</option>)}
              </select>
            </label>
            <label>
              Поиск
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Объект, пользователь, значение" />
            </label>
          </div>
          <div className="budgets-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Событие</th>
                  <th>Объект</th>
                  <th>Автор</th>
                  <th>Клиент</th>
                  <th>Детали</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((item) => (
                  <tr key={item.id}>
                    <td>{new Date(item.created_at).toLocaleString("ru-RU")}</td>
                    <td><span className="badge">{readableEvent(item.event_type)}</span></td>
                    <td>{item.resource_type}<div className="muted-note">{item.resource_id || "—"}</div></td>
                    <td>{item.actor_role || "system"}<div className="muted-note">{item.actor_user_id || ""}</div></td>
                    <td>{item.tenant_client_id || "—"}</td>
                    <td><code className="audit-payload">{JSON.stringify(item.payload)}</code></td>
                  </tr>
                ))}
                {!filtered.length ? <tr><td colSpan={6} className="muted-note">Событий не найдено.</td></tr> : null}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
