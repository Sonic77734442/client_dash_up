"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson } from "../../../lib/api";
import {
  hasOptionalStringFields,
  hasStringFields,
  normalizeListPayload,
} from "../../../lib/listPayload";
import { AuthUser, ClientOut, UserClientAccessOut } from "../../../lib/types";

function isAuthUserItem(value: unknown): value is AuthUser {
  return (
    hasStringFields(value, ["id", "name", "role", "status"]) &&
    hasOptionalStringFields(value, ["email"])
  );
}

function isClientItem(value: unknown): value is ClientOut {
  return hasStringFields(value, ["id", "name"]);
}

function isAccessItem(value: unknown): value is UserClientAccessOut {
  return hasStringFields(value, ["id", "user_id", "client_id", "role", "updated_at"]);
}

function userRoleLabel(role?: AuthUser["role"]) {
  if (role === "agency") return "Сотрудник агентства";
  if (role === "solo_client") return "Соло-владелец";
  if (role === "client") return "Клиент · только просмотр";
  if (role === "admin") return "Администратор";
  return "Неизвестная роль";
}

function userRoleShortLabel(role?: AuthUser["role"]) {
  if (role === "agency") return "Агентство";
  if (role === "solo_client") return "Соло-владелец";
  if (role === "client") return "Клиент";
  if (role === "admin") return "Администратор";
  return "—";
}

function effectiveAccessCopy(role?: AuthUser["role"]) {
  if (role === "agency") {
    return {
      title: "Работа с клиентом через агентство",
      description:
        "Пользователь видит клиентов из портфеля своего агентства. Владелец или менеджер подключает Meta Ads и Google Ads, а участник обновляет уже добавленные аккаунты.",
    };
  }
  if (role === "solo_client") {
    return {
      title: "Управление своим рабочим пространством",
      description:
        "Пользователь видит метрики и сам управляет подключениями, импортом аккаунтов, синхронизацией и бюджетами только этого клиента.",
    };
  }
  return {
    title: "Только просмотр данных клиента",
    description:
      "Пользователь видит дашборды, отчёты и метрики назначенного клиента, но не подключает источники и не запускает синхронизацию.",
  };
}

function accessSourceLabel(item: UserClientAccessOut, role?: AuthUser["role"]) {
  if (item.role === "agency") return "Через агентство";
  if (role === "solo_client") return "Соло-кабинет";
  return "Прямое назначение";
}

function connectionCountLabel(count: number) {
  const lastTwo = count % 100;
  const last = count % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return `${count} назначений`;
  if (last === 1) return `${count} назначение`;
  if (last >= 2 && last <= 4) return `${count} назначения`;
  return `${count} назначений`;
}

export default function PlatformAccessMapPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [access, setAccess] = useState<UserClientAccessOut[]>([]);
  const [userId, setUserId] = useState("");
  const [clientId, setClientId] = useState("");
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [revokingId, setRevokingId] = useState("");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [userRows, clientRows, accessRows] = await Promise.all([
        req<unknown>("/auth/internal/users"),
        req<unknown>("/clients?status=all"),
        req<unknown>("/auth/internal/access"),
      ]);
      const nextUsers = normalizeListPayload(userRows, isAuthUserItem, "пользователей");
      const nextClients = normalizeListPayload(clientRows, isClientItem, "клиентов");
      const nextAccess = normalizeListPayload(accessRows, isAccessItem, "назначений доступа");
      const assignableUsers = nextUsers.filter((item) => item.role !== "admin" && item.status === "active");
      const assignableClients = nextClients.filter((item) => item.status === "active");

      setUsers(nextUsers);
      setClients(nextClients);
      setAccess(nextAccess);
      setUserId((current) =>
        assignableUsers.some((item) => item.id === current) ? current : assignableUsers[0]?.id || ""
      );
      setClientId((current) =>
        assignableClients.some((item) => item.id === current) ? current : assignableClients[0]?.id || ""
      );
      setWarning("");
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить карту доступов");
    } finally {
      setLoading(false);
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
      return `${user?.name || ""} ${user?.email || ""} ${userRoleLabel(user?.role)} ${client?.name || ""}`
        .toLowerCase()
        .includes(query);
    });
  }, [access, clientsById, search, usersById]);

  const selectedUser = usersById.get(userId);
  const activeClients = useMemo(
    () => clients.filter((item) => item.status === "active"),
    [clients]
  );
  const selectedIsAgency = selectedUser?.role === "agency";
  const selectedIsSoloClient = selectedUser?.role === "solo_client";
  const selectedSoloAssignment = selectedIsSoloClient
    ? access.find(
        (item) => item.user_id === userId && clientsById.get(item.client_id)?.status === "active"
      )
    : undefined;
  const effectiveClientId = selectedSoloAssignment?.client_id || clientId;
  const selectedExistingAssignment = access.find(
    (item) => item.user_id === userId && item.client_id === effectiveClientId
  );
  const selectedAccessCopy = effectiveAccessCopy(selectedUser?.role);
  const canAssign = Boolean(
    selectedUser &&
      effectiveClientId &&
      !selectedIsAgency &&
      !selectedExistingAssignment &&
      selectedUser.status === "active"
  );

  async function assignAccess() {
    if (!userId || !effectiveClientId) {
      push("Выберите пользователя и клиента", "error");
      return;
    }
    if (selectedIsAgency) {
      push("Доступ сотрудникам выдаётся через раздел «Агентства»", "error");
      return;
    }
    try {
      setSaving(true);
      await req<UserClientAccessOut>("/auth/internal/access", {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          client_id: effectiveClientId,
          role: "client",
        }),
      });
      await loadData();
      push("Клиент назначен пользователю", "success");
    } catch (error) {
      push(error instanceof Error ? error.message : "Не удалось назначить клиента", "error");
    } finally {
      setSaving(false);
    }
  }

  async function revokeAccess(item: UserClientAccessOut) {
    const user = usersById.get(item.user_id);
    const client = clientsById.get(item.client_id);
    if (!user || item.role === "agency") return;
    const soloWarning = user.role === "solo_client"
      ? " До нового назначения соло-владелец не сможет открыть рабочий кабинет."
      : "";
    if (!window.confirm(
      `Отозвать у «${user.name}» доступ к клиенту «${client?.name || item.client_id}»? Клиент и его данные не удалятся.${soloWarning}`
    )) return;

    try {
      setRevokingId(item.id);
      await req<{ status: string }>(`/auth/internal/access/${item.user_id}/${item.client_id}`, {
        method: "DELETE",
      });
      await loadData();
      push("Доступ отозван", "success");
    } catch (error) {
      push(error instanceof Error ? error.message : "Не удалось отозвать доступ", "error");
    } finally {
      setRevokingId("");
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
              <div className="topbar-title">Доступы к клиентам</div>
              <div className="panel-subtitle">Назначьте, кто сможет открыть данные каждого клиента</div>
            </div>
            <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
          </header>

          <div
            className={`warning ${warning ? "" : "hidden"}`}
            role={warning ? "alert" : undefined}
            aria-live="polite"
          >
            {warning}
          </div>

          <section className="panel access-guide" aria-labelledby="access-guide-title">
            <div className="access-guide-head">
              <div>
                <div className="access-eyebrow">Как это работает</div>
                <h2 id="access-guide-title">Права складываются из трёх частей</h2>
              </div>
              <div className="access-guide-rule">
                Администратор видит всех клиентов автоматически — назначать его здесь не нужно.
              </div>
            </div>
            <div className="access-guide-steps">
              <article className="access-guide-step">
                <span>1</span>
                <div>
                  <strong>Тип пользователя</strong>
                  <p>Определяет общий режим: агентство, просмотр клиента или собственный кабинет.</p>
                  <Link href="/platform/users">Настроить пользователей</Link>
                </div>
              </article>
              <article className="access-guide-step">
                <span>2</span>
                <div>
                  <strong>Доступный клиент</strong>
                  <p>Определяет, чьи рекламные аккаунты, метрики и отчёты увидит пользователь.</p>
                  <a href="#assign-client-access">Назначить ниже</a>
                </div>
              </article>
              <article className="access-guide-step">
                <span>3</span>
                <div>
                  <strong>Роль внутри агентства</strong>
                  <p>Владелец или менеджер подключает Meta Ads и Google Ads и ищет новые аккаунты.</p>
                  <Link href="/platform/agencies">Настроить агентства</Link>
                </div>
              </article>
            </div>
          </section>

          <section className="panel access-assign-panel" id="assign-client-access">
            <div className="access-assign-intro">
              <div className="access-eyebrow">Новое назначение</div>
              <h3>Кому предоставить доступ к данным клиента</h3>
              <div className="panel-subtitle">
                Сначала выберите пользователя. Платформа сама покажет правильный способ выдачи доступа.
              </div>
            </div>
            <div className="access-assignment-workspace">
              <div className="access-assign-controls">
                <label>
                  <span>1. Пользователь</span>
                  <select
                    aria-label="Пользователь для доступа"
                    value={userId}
                    onChange={(event) => setUserId(event.target.value)}
                  >
                    {loading ? <option value="">Загружаем пользователей…</option> : null}
                    {!loading && !users.some((item) => item.role !== "admin" && item.status === "active") ? (
                      <option value="">Нет активных пользователей</option>
                    ) : null}
                    {!loading ? users
                      .filter((item) => item.role !== "admin" && item.status === "active")
                      .map((item) => (
                        <option value={item.id} key={item.id}>
                          {item.name} · {userRoleShortLabel(item.role)} · {item.email || "без email"}
                        </option>
                      )) : null}
                  </select>
                </label>

                {!selectedIsAgency ? (
                  <label>
                    <span>2. Клиент</span>
                    <select
                      aria-label="Клиент для доступа"
                      value={effectiveClientId}
                      onChange={(event) => setClientId(event.target.value)}
                      disabled={Boolean(selectedSoloAssignment)}
                    >
                      {loading ? <option value="">Загружаем клиентов…</option> : null}
                      {!loading && !activeClients.length ? <option value="">Нет активных клиентов</option> : null}
                      {!loading ? activeClients.map((item) => <option value={item.id} key={item.id}>{item.name}</option>) : null}
                    </select>
                  </label>
                ) : null}

                {!selectedIsAgency ? (
                  <button
                    className="primary-btn access-assign-submit"
                    disabled={!canAssign || saving}
                    onClick={() => void assignAccess()}
                  >
                    {saving ? "Назначаем…" : selectedExistingAssignment ? "Уже назначен" : "Открыть доступ"}
                  </button>
                ) : (
                  <Link className="primary-btn access-assign-submit" href="/platform/agencies">
                    Перейти в агентства
                  </Link>
                )}
              </div>

              {selectedUser ? (
                <div className={`access-result-card ${selectedIsAgency ? "agency" : selectedIsSoloClient ? "solo" : "viewer"}`}>
                  <div className="access-result-icon" aria-hidden="true">i</div>
                  <div>
                    <div className="access-result-kicker">После назначения будет доступно</div>
                    <strong>{selectedAccessCopy.title}</strong>
                    <p>{selectedAccessCopy.description}</p>
                    {selectedIsAgency ? (
                      <small>
                        Клиентов добавляют в портфель агентства, а возможности сотрудника задают его ролью в команде.
                      </small>
                    ) : selectedIsSoloClient && selectedSoloAssignment ? (
                      <small>
                        Соло-владелец уже закреплён за клиентом «{clientsById.get(selectedSoloAssignment.client_id)?.name || "Без названия"}». Сначала отзовите эту связь, если нужно назначить другого клиента.
                      </small>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          </section>

          <section className="panel access-list-panel">
            <div className="panel-head access-list-head">
              <div>
                <h3>Кто к каким клиентам имеет доступ</h3>
                <div className="panel-subtitle">
                  {connectionCountLabel(rows.length)}. Здесь показан итог прямых назначений и настроек агентств.
                </div>
              </div>
              <input
                className="clientops-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Найти пользователя или клиента"
                aria-label="Поиск по доступам"
              />
            </div>
            <div className="access-mobile-hint">
              На телефоне таблицу можно двигать влево и вправо. Кнопка действия остаётся справа.
            </div>
            <div className="budgets-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Пользователь</th>
                    <th>Тип пользователя</th>
                    <th>Клиент</th>
                    <th>Способ доступа</th>
                    <th>Где управлять</th>
                    <th>Обновлено</th>
                    <th aria-label="Действия"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => {
                    const user = usersById.get(item.user_id);
                    const client = clientsById.get(item.client_id);
                    const isAgencyAccess = item.role === "agency";
                    const userInactive = user?.status !== "active";
                    const clientInactive = client?.status !== "active";
                    return (
                      <tr key={item.id} className={userInactive || clientInactive ? "access-row-inactive" : undefined}>
                        <td>
                          <strong>{user?.name || item.user_id}</strong>
                          <div className="muted-note">{user?.email || "Email не указан"}</div>
                          {userInactive ? <div className="muted-note">Пользователь отключён — войти не может</div> : null}
                        </td>
                        <td>{userRoleLabel(user?.role)}</td>
                        <td>
                          <strong>{client?.name || item.client_id}</strong>
                          {clientInactive ? (
                            <div className="muted-note">Клиент в архиве — данные недоступны</div>
                          ) : null}
                        </td>
                        <td>
                          <span className={`badge ${user?.role === "solo_client" ? "good" : ""}`}>
                            {accessSourceLabel(item, user?.role)}
                          </span>
                        </td>
                        <td>
                          {isAgencyAccess ? (
                            <Link className="access-inline-link" href="/platform/agencies">Найти в разделе «Агентства»</Link>
                          ) : (
                            <span>На этой странице</span>
                          )}
                        </td>
                        <td>{new Date(item.updated_at).toLocaleString("ru-RU")}</td>
                        <td className="access-row-action">
                          {isAgencyAccess ? (
                            <Link className="mini-btn" href="/platform/agencies">К агентствам</Link>
                          ) : (
                            <button
                              className="mini-btn access-revoke-btn"
                              disabled={revokingId === item.id}
                              onClick={() => void revokeAccess(item)}
                            >
                              {revokingId === item.id ? "Отзываем…" : "Отозвать"}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {!rows.length ? (
                    <tr>
                      <td colSpan={7} className="access-empty-state">
                        <strong>{loading ? "Загружаем назначения…" : "Назначений не найдено"}</strong>
                        {!loading ? (
                          <span>{search ? "Измените запрос поиска." : "Выдайте первый доступ выше или настройте клиентов агентства."}</span>
                        ) : null}
                      </td>
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
