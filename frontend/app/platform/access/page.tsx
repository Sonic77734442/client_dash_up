"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson } from "../../../lib/api";
import { AuthUser, ClientOut, UserClientAccessOut } from "../../../lib/types";

export default function PlatformAccessMapPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [access, setAccess] = useState<UserClientAccessOut[]>([]);
  const [userId, setUserId] = useState("");
  const [clientId, setClientId] = useState("");
  const [role, setRole] = useState<"agency" | "client">("client");
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    try {
      const [userRows, clientRows, accessRows] = await Promise.all([
        req<{ items: AuthUser[] }>("/auth/internal/users"),
        req<{ items: ClientOut[] }>("/clients?status=all"),
        req<{ items: UserClientAccessOut[] }>("/auth/internal/access"),
      ]);
      setUsers(userRows.items || []);
      setClients(clientRows.items || []);
      setAccess(accessRows.items || []);
      setUserId((current) => current || userRows.items?.find((item) => item.role !== "admin")?.id || "");
      setClientId((current) => current || clientRows.items?.[0]?.id || "");
      setWarning("");
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить карту доступов");
    }
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadData();
  }, [ready, loadData]);

  const usersById = useMemo(() => new Map(users.map((item) => [item.id, item])), [users]);
  const clientsById = useMemo(() => new Map(clients.map((item) => [item.id, item])), [clients]);
  const rows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return access.filter((item) => {
      if (!query) return true;
      const user = usersById.get(item.user_id);
      const client = clientsById.get(item.client_id);
      return `${user?.name || ""} ${user?.email || ""} ${client?.name || ""} ${item.role}`
        .toLowerCase()
        .includes(query);
    });
  }, [access, clientsById, search, usersById]);

  async function assignAccess() {
    if (!userId || !clientId) {
      push("Выберите пользователя и клиента", "error");
      return;
    }
    try {
      await req<UserClientAccessOut>("/auth/internal/access", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, client_id: clientId, role }),
      });
      await loadData();
      push("Доступ назначен", "success");
    } catch (error) {
      push(error instanceof Error ? error.message : "Не удалось назначить доступ", "error");
    }
  }

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="platform_admin" />
        <main className="content">
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs active="platform_admin" />
              <div className="topbar-title">Карта доступов</div>
              <div className="panel-subtitle">Кто видит клиентов и их рекламные данные</div>
            </div>
            <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="panel access-assign-panel">
            <div>
              <h3>Выдать доступ</h3>
              <div className="panel-subtitle">Повторное назначение той же связи безопасно обновляет её.</div>
            </div>
            <div className="access-assign-controls">
              <label>
                Пользователь
                <select value={userId} onChange={(event) => setUserId(event.target.value)}>
                  {users.filter((item) => item.role !== "admin").map((item) => (
                    <option value={item.id} key={item.id}>{item.name} · {item.email || item.role}</option>
                  ))}
                </select>
              </label>
              <label>
                Клиент
                <select value={clientId} onChange={(event) => setClientId(event.target.value)}>
                  {clients.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
                </select>
              </label>
              <label>
                Уровень
                <select value={role} onChange={(event) => setRole(event.target.value as "agency" | "client")}>
                  <option value="client">Клиент — только свой контур</option>
                  <option value="agency">Агентство — рабочий доступ</option>
                </select>
              </label>
              <button className="primary-btn" onClick={() => void assignAccess()}>Назначить</button>
            </div>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div>
                <h3>Фактические назначения</h3>
                <div className="panel-subtitle">{rows.length} связей</div>
              </div>
              <input
                className="clientops-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Пользователь или клиент"
              />
            </div>
            <div className="budgets-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Пользователь</th>
                    <th>Глобальная роль</th>
                    <th>Клиент</th>
                    <th>Доступ</th>
                    <th>Обновлено</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => {
                    const user = usersById.get(item.user_id);
                    const client = clientsById.get(item.client_id);
                    return (
                      <tr key={item.id}>
                        <td>{user?.name || item.user_id}<div className="muted-note">{user?.email || ""}</div></td>
                        <td>{user?.role || "—"}</td>
                        <td>{client?.name || item.client_id}</td>
                        <td><span className="badge">{item.role === "agency" ? "Рабочий" : "Просмотр"}</span></td>
                        <td>{new Date(item.updated_at).toLocaleString("ru-RU")}</td>
                      </tr>
                    );
                  })}
                  {!rows.length ? <tr><td colSpan={5} className="muted-note">Назначений не найдено.</td></tr> : null}
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
