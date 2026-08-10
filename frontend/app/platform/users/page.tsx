"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson } from "../../../lib/api";
import { AuthMeResponse } from "../../../lib/types";

type UserItem = {
  id: string;
  email?: string | null;
  name: string;
  role: "admin" | "agency" | "client" | "solo_client";
  status: "active" | "inactive";
  created_at?: string;
  updated_at?: string;
};

function fmtDate(v?: string) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function roleLabel(role: "admin" | "agency" | "client" | "solo_client") {
  if (role === "admin") return "Администратор";
  if (role === "agency") return "Агентство";
  if (role === "solo_client") return "Соло-владелец";
  return "Клиент";
}

export default function PlatformUsersPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [users, setUsers] = useState<UserItem[]>([]);
  const [currentUserId, setCurrentUserId] = useState<string>("");
  const [search, setSearch] = useState("");
  const [createEmail, setCreateEmail] = useState("");
  const [createName, setCreateName] = useState("");
  const [createRole, setCreateRole] = useState<"admin" | "agency" | "client" | "solo_client">("client");
  const [createStatus, setCreateStatus] = useState<"active" | "inactive">("active");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadUsers = useCallback(async () => {
    const me = await req<AuthMeResponse>("/auth/me");
    setCurrentUserId(me.user.id);
    const rows = await req<{ items: UserItem[] }>("/auth/internal/users");
    setUsers(rows.items || []);
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadUsers().catch((err) => setWarning(err instanceof Error ? err.message : "Не удалось загрузить пользователей"));
  }, [ready, loadUsers]);

  async function createUser() {
    if (!createName.trim()) {
      push("Укажите имя", "error");
      return;
    }
    try {
      await req<UserItem>("/auth/internal/users", {
        method: "POST",
        body: JSON.stringify({
          email: createEmail.trim() || null,
          name: createName.trim(),
          role: createRole,
          status: createStatus,
        }),
      });
      setCreateEmail("");
      setCreateName("");
      setCreateRole("client");
      setCreateStatus("active");
      await loadUsers();
      push("Пользователь создан", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось создать пользователя", "error");
    }
  }

  async function patchUser(userId: string, patch: Partial<Pick<UserItem, "role" | "status">>) {
    try {
      await req<UserItem>(`/auth/internal/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      await loadUsers();
      push("Пользователь обновлён", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось обновить пользователя", "error");
    }
  }

  async function deleteUser(user: UserItem) {
    const label = user.email || user.name || user.id;
    if (!window.confirm(`Удалить пользователя ${label}? Это действие нельзя отменить.`)) return;
    try {
      await req<{ status: string }>(`/auth/internal/users/${user.id}`, {
        method: "DELETE",
      });
      await loadUsers();
      push("Пользователь удалён", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось удалить пользователя", "error");
    }
  }

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return users;
    return users.filter((u) => `${u.name} ${u.email || ""} ${u.role} ${u.status}`.toLowerCase().includes(q));
  }, [users, search]);

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="platform_admin" subtitle="Управление платформой" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="platform_admin" />
              <div className="topbar-title">Пользователи платформы</div>
            </div>
            <div className="session-controls">
              <a className="ghost-btn" href="/platform/agencies">Агентства</a>
              <a className="ghost-btn" href="/platform/alerts">Инциденты</a>
              {tokenLoginEnabled ? (
                <>
                <input value={session.apiBase} onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))} placeholder="Адрес API" />
                <input type="password" value={session.token} onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))} placeholder="Токен сессии" />
                <button
                  className="ghost-btn"
                  onClick={async () => {
                    const next = { apiBase: session.apiBase.trim().replace(/\/$/, "") || defaultApiBase, token: session.token.trim() };
                    persist(next);
                    setSession(next);
                    await loadUsers();
                    push("Сессия сохранена", "success");
                  }}
                >
                  Сохранить
                </button>
                </>
              ) : null}
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>Новый пользователь</h3>
                <div className="panel-subtitle">Создайте пользователя и назначьте ему роль на платформе.</div>
              </div>
            </div>
            <div className="session-controls" style={{ marginTop: 10 }}>
              <input type="email" value={createEmail} onChange={(e) => setCreateEmail(e.target.value)} placeholder="email@company.com (необязательно)" />
              <input value={createName} onChange={(e) => setCreateName(e.target.value)} placeholder="Имя и фамилия" />
              <select value={createRole} onChange={(e) => setCreateRole(e.target.value as UserItem["role"])}>
                <option value="client">Клиент — только просмотр</option>
                <option value="solo_client">Соло-владелец — подключения и синхронизация</option>
                <option value="agency">Агентство</option>
                <option value="admin">Администратор</option>
              </select>
              <select value={createStatus} onChange={(e) => setCreateStatus(e.target.value as "active" | "inactive")}>
                <option value="active">Активен</option>
                <option value="inactive">Неактивен</option>
              </select>
              <button className="primary-btn" onClick={() => void createUser()}>Создать</button>
            </div>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="chip-row" style={{ marginTop: 0 }}>
              <input className="clientops-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по имени, email, роли или статусу" />
              <button className="ghost-btn" onClick={() => void loadUsers()}>Обновить</button>
            </div>
            <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
              <table className="budgets-table">
                <thead>
                  <tr>
                    <th>Имя</th>
                    <th>Email</th>
                    <th>Роль</th>
                    <th>Статус</th>
                    <th>Обновлён</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((u) => (
                    <tr key={u.id}>
                      <td>{u.name}</td>
                      <td>{u.email || "--"}</td>
                      <td>
                        <select value={u.role} onChange={(e) => void patchUser(u.id, { role: e.target.value as UserItem["role"] })}>
                          <option value="admin">{roleLabel("admin")}</option>
                          <option value="agency">{roleLabel("agency")}</option>
                          <option value="solo_client">{roleLabel("solo_client")}</option>
                          <option value="client">{roleLabel("client")}</option>
                        </select>
                      </td>
                      <td>
                        <select value={u.status} onChange={(e) => void patchUser(u.id, { status: e.target.value as UserItem["status"] })}>
                          <option value="active">Активен</option>
                          <option value="inactive">Неактивен</option>
                        </select>
                      </td>
                      <td>{fmtDate(u.updated_at || u.created_at)}</td>
                      <td>
                        <div className="alert-actions">
                          <button
                            className="mini-btn"
                            onClick={() => void patchUser(u.id, { status: u.status === "active" ? "inactive" : "active" })}
                          >
                            {u.status === "active" ? "Отключить" : "Активировать"}
                          </button>
                          <button
                            className="mini-btn"
                            disabled={u.id === currentUserId}
                            onClick={() => void deleteUser(u)}
                          >
                            Удалить
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!filtered.length ? (
                    <tr>
                      <td colSpan={6} className="muted-note">Пользователи не найдены.</td>
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
