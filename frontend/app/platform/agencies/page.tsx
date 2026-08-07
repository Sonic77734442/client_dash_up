"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson } from "../../../lib/api";
import {
  getSessionToken,
  setImpersonationReturnSession,
  setSessionToken,
} from "../../../lib/sessionToken";
import {
  AgencyInviteIssueResponse,
  AgencyInviteOut,
  AgencyMemberOut,
  AgencyOut,
  AuthMeResponse,
  SessionContext,
} from "../../../lib/types";

type UserItem = {
  id: string;
  email?: string | null;
  name: string;
  role: "admin" | "agency" | "client";
  status: "active" | "inactive";
};

type SessionIssueResponse = {
  token: string;
  session_id: string;
  user_id: string;
  expires_at: string;
};

const LS_API_BASE = "ops_api_base";
const SESSION_UPDATED_EVENT = "ops-session-updated";

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function agencyRoleLabel(v: "owner" | "manager" | "member") {
  if (v === "owner") return "Владелец";
  if (v === "manager") return "Менеджер";
  return "Участник";
}

function agencyStatusLabel(v: string) {
  return v === "active" ? "Активно" : v === "suspended" ? "Приостановлено" : v;
}

function memberStatusLabel(v: string) {
  return v === "active" ? "активен" : v === "inactive" ? "неактивен" : v;
}

function inviteStatusLabel(v: string) {
  if (v === "pending") return "ожидает";
  if (v === "accepted") return "принято";
  if (v === "expired") return "истекло";
  if (v === "revoked") return "отозвано";
  return v;
}

function userRoleLabel(v: UserItem["role"]) {
  if (v === "admin") return "Администратор";
  if (v === "agency") return "Агентство";
  return "Клиент";
}

export default function PlatformAgenciesPage() {
  const router = useRouter();
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [ctx, setCtx] = useState<SessionContext | null>(null);

  const [agencies, setAgencies] = useState<AgencyOut[]>([]);
  const [selectedAgencyId, setSelectedAgencyId] = useState<string | null>(null);

  const [members, setMembers] = useState<AgencyMemberOut[]>([]);
  const [invites, setInvites] = useState<AgencyInviteOut[]>([]);

  const [users, setUsers] = useState<UserItem[]>([]);

  const [createOpen, setCreateOpen] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSlug, setCreateSlug] = useState("");
  const [createPlan, setCreatePlan] = useState("starter");
  const [createAllowClientInvites, setCreateAllowClientInvites] = useState(true);

  const [memberUserId, setMemberUserId] = useState("");
  const [memberRole, setMemberRole] = useState<"owner" | "manager" | "member">("member");
  const [memberStatus, setMemberStatus] = useState<"active" | "inactive">("active");

  const [inviteEmail, setInviteEmail] = useState("");
  const [lastInviteUrl, setLastInviteUrl] = useState("");

  const selectedAgency = useMemo(
    () => agencies.find((x) => x.id === selectedAgencyId) || null,
    [agencies, selectedAgencyId]
  );

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadRefData = useCallback(async () => {
    try {
      const usersRes = await req<{ items: UserItem[] }>("/auth/internal/users");
      setUsers((usersRes.items || []).filter((u) => u.role === "agency" || u.role === "admin"));
    } catch {
      setUsers([]);
    }
  }, [req]);

  const loadAgencies = useCallback(async () => {
    const res = await req<{ items: AgencyOut[] }>("/platform/agencies?status=all");
    const rows = res.items || [];
    setAgencies(rows);
    if (!selectedAgencyId && rows[0]) {
      setSelectedAgencyId(rows[0].id);
    } else if (selectedAgencyId && !rows.some((x) => x.id === selectedAgencyId)) {
      setSelectedAgencyId(rows[0]?.id || null);
    }
  }, [req, selectedAgencyId]);

  const loadAgencyDetails = useCallback(
    async (agencyId: string) => {
      const m = await req<AgencyMemberOut[]>(`/platform/agencies/${agencyId}/members`);
      let i: AgencyInviteOut[] = [];
      try {
        i = await req<AgencyInviteOut[]>(`/platform/agencies/${agencyId}/invites?status=all`);
      } catch {
        i = [];
      }
      setMembers(m || []);
      setInvites(i || []);
    },
    [req]
  );

  const reloadAll = useCallback(async () => {
    const me = await req<AuthMeResponse>("/auth/me");
    setCtx(me.session);

    await Promise.all([loadRefData(), loadAgencies()]);
  }, [loadAgencies, loadRefData, req]);

  useEffect(() => {
    if (!ready) return;
    void reloadAll().catch((err) => {
      setWarning(err instanceof Error ? err.message : "Не удалось загрузить данные управления платформой");
    });
  }, [ready, reloadAll]);

  useEffect(() => {
    if (!selectedAgencyId) {
      setMembers([]);
      setInvites([]);
      return;
    }
    void loadAgencyDetails(selectedAgencyId).catch((err) => {
      setWarning(err instanceof Error ? err.message : "Не удалось загрузить данные агентства");
    });
  }, [selectedAgencyId, loadAgencyDetails]);

  const kpis = useMemo(() => {
    const active = agencies.filter((x) => x.status === "active").length;
    const suspended = agencies.filter((x) => x.status === "suspended").length;
    const totalMembers = members.length;
    return { total: agencies.length, active, suspended, totalMembers };
  }, [agencies, members.length]);

  const usersById = useMemo(() => {
    const map = new Map<string, UserItem>();
    for (const u of users) map.set(u.id, u);
    return map;
  }, [users]);

  const selectedStats = useMemo(() => {
    const activeMembers = members.filter((m) => m.status === "active").length;
    return {
      totalMembers: members.length,
      activeMembers,
    };
  }, [members]);

  async function createAgency() {
    if (!createName.trim()) {
      push("Укажите название агентства", "error");
      return;
    }
    try {
      setCreateLoading(true);
      const created = await req<AgencyOut>("/platform/agencies", {
        method: "POST",
        body: JSON.stringify({
          name: createName.trim(),
          slug: createSlug.trim() || undefined,
          status: "active",
          plan: createPlan.trim() || "starter",
          allow_client_invites: createAllowClientInvites,
        }),
      });
      setCreateOpen(false);
      setCreateName("");
      setCreateSlug("");
      setCreatePlan("starter");
      setCreateAllowClientInvites(true);
      await loadAgencies();
      setSelectedAgencyId(created.id);
      push("Агентство создано", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось создать агентство", "error");
    } finally {
      setCreateLoading(false);
    }
  }

  async function setAgencyStatus(status: "active" | "suspended") {
    if (!selectedAgency) return;
    try {
      await req<AgencyOut>(`/platform/agencies/${selectedAgency.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await loadAgencies();
      push(`Статус агентства: ${agencyStatusLabel(status)}`, "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось обновить агентство", "error");
    }
  }

  async function setClientInvitesAllowed(allow: boolean) {
    if (!selectedAgency) return;
    try {
      await req<AgencyOut>(`/platform/agencies/${selectedAgency.id}`, {
        method: "PATCH",
        body: JSON.stringify({ allow_client_invites: allow }),
      });
      await loadAgencies();
      push(allow ? "Приглашения клиентов включены" : "Приглашения клиентов отключены", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось обновить агентство", "error");
    }
  }

  async function deleteAgency() {
    if (!selectedAgency) return;
    if (!window.confirm(`Удалить агентство ${selectedAgency.name}? Участники будут отвязаны, но не удалены.`)) return;
    try {
      await req<{ status: string }>(`/platform/agencies/${selectedAgency.id}`, {
        method: "DELETE",
      });
      await loadAgencies();
      setMembers([]);
      setInvites([]);
      push("Агентство удалено", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось удалить агентство", "error");
    }
  }

  async function upsertMember() {
    if (!selectedAgency || !memberUserId) return;
    try {
      await req<AgencyMemberOut>(`/platform/agencies/${selectedAgency.id}/members`, {
        method: "POST",
        body: JSON.stringify({ user_id: memberUserId, role: memberRole, status: memberStatus }),
      });
      await loadAgencyDetails(selectedAgency.id);
      push("Участник обновлён", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось обновить участника", "error");
    }
  }

  async function issueInvite() {
    if (!selectedAgency || !inviteEmail.trim()) {
      push("Укажите email для приглашения", "error");
      return;
    }
    try {
      const issued = await req<AgencyInviteIssueResponse>(`/platform/agencies/${selectedAgency.id}/invites`, {
        method: "POST",
        body: JSON.stringify({
          email: inviteEmail.trim().toLowerCase(),
          member_role: "member",
          expires_in_days: 7,
        }),
      });
      setLastInviteUrl(issued.accept_url);
      await loadAgencyDetails(selectedAgency.id);
      setInviteEmail("");
      push("Приглашение создано", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось создать приглашение", "error");
    }
  }

  async function copyInviteUrl() {
    if (!lastInviteUrl) return;
    try {
      await navigator.clipboard.writeText(lastInviteUrl);
      push("Ссылка приглашения скопирована", "success");
    } catch {
      push("Не удалось скопировать ссылку", "error");
    }
  }

  async function deactivateMember(memberId: string) {
    if (!selectedAgency) return;
    try {
      await req<AgencyMemberOut>(`/platform/agencies/${selectedAgency.id}/members/${memberId}/deactivate`, {
        method: "POST",
      });
      await loadAgencyDetails(selectedAgency.id);
      push("Участник отключён", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось отключить участника", "error");
    }
  }

  async function removeMember(memberId: string) {
    if (!selectedAgency) return;
    try {
      await req<{ status: string }>(`/platform/agencies/${selectedAgency.id}/members/${memberId}`, {
        method: "DELETE",
      });
      await loadAgencyDetails(selectedAgency.id);
      push("Участник удалён из агентства", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось удалить участника", "error");
    }
  }

  async function revokeInvite(inviteId: string) {
    if (!selectedAgency) return;
    try {
      await req<AgencyInviteOut>(`/platform/agencies/${selectedAgency.id}/invites/${inviteId}/revoke`, {
        method: "POST",
      });
      await loadAgencyDetails(selectedAgency.id);
      push("Приглашение отозвано", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось отозвать приглашение", "error");
    }
  }

  async function resendInvite(inviteId: string) {
    if (!selectedAgency) return;
    try {
      const issued = await req<AgencyInviteIssueResponse>(`/platform/agencies/${selectedAgency.id}/invites/${inviteId}/resend`, {
        method: "POST",
        body: JSON.stringify({ expires_in_days: 7 }),
      });
      setLastInviteUrl(issued.accept_url);
      await loadAgencyDetails(selectedAgency.id);
      push("Приглашение отправлено повторно", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось отправить приглашение повторно", "error");
    }
  }

  async function openAsAgencyUser(userId: string) {
    const user = usersById.get(userId);
    if (!selectedAgency || !user) return;
    if (user.role !== "agency") {
      push("Выберите пользователя агентства, чтобы открыть его рабочее пространство", "error");
      return;
    }
    try {
      const currentToken = session.token || getSessionToken();
      const currentApiBase = session.apiBase || localStorage.getItem(LS_API_BASE) || defaultApiBase;
      const issued = await req<SessionIssueResponse>("/auth/internal/sessions/issue", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, ttl_minutes: 1440 }),
      });
      setImpersonationReturnSession(currentToken, currentApiBase, `${user.name} / ${selectedAgency.name}`);
      localStorage.setItem(LS_API_BASE, currentApiBase);
      setSessionToken(issued.token);
      window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
      router.push("/");
    } catch (err) {
      push(err instanceof Error ? err.message : "Не удалось открыть рабочее пространство агентства", "error");
    }
  }

  const adminOnly = ctx && ctx.role !== "admin";
  const canManageMembers = useMemo(() => {
    if (!ctx) return false;
    if (ctx.role === "admin") return true;
    if (ctx.role !== "agency" || !ctx.user_id) return false;
    const me = members.find((m) => m.user_id === ctx.user_id);
    if (!me || me.status !== "active") return false;
    return me.role === "owner" || me.role === "manager";
  }, [ctx, members]);
  const canActivate = selectedAgency?.status === "suspended";
  const canSuspend = selectedAgency?.status === "active";

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="platform_admin" subtitle="Управление платформой" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="platform_admin" />
              <div className="topbar-title">Управление агентствами</div>
              <div className="panel-subtitle">Создавайте агентства, добавляйте участников и управляйте доступом.</div>
            </div>
            <div className="session-controls">
              <a className="ghost-btn" href="/platform/users">Пользователи</a>
              <a className="ghost-btn" href="/platform/alerts">Инциденты</a>
              {tokenLoginEnabled ? (
                <>
                  <input
                    type="text"
                    value={session.apiBase}
                    onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))}
                    placeholder="Адрес API"
                  />
                  <input
                    type="password"
                    value={session.token}
                    onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))}
                    placeholder="Токен сессии"
                  />
                  <button
                    className="ghost-btn"
                    onClick={async () => {
                      const next = { apiBase: session.apiBase.trim().replace(/\/$/, "") || defaultApiBase, token: session.token.trim() };
                      persist(next);
                      setSession(next);
                      try {
                        await reloadAll();
                        push("Сессия сохранена", "success");
                      } catch (err) {
                        setWarning(err instanceof Error ? err.message : "Не удалось загрузить данные");
                      }
                    }}
                    disabled={!ready}
                  >
                    Сохранить
                  </button>
                </>
              ) : null}
              <button className="primary-btn" onClick={() => setCreateOpen(true)} disabled={adminOnly === true}>Создать агентство</button>
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>
          {adminOnly ? <div className="warning" style={{ marginTop: 10 }}>Этот раздел доступен только администраторам.</div> : null}

          <section className="agency-flow" style={{ marginTop: 12 }}>
            <div className="agency-flow-step">1. Выберите агентство</div>
            <div className="agency-flow-step">2. Добавьте участника и роль</div>
            <div className="agency-flow-step">3. Пригласите пользователя агентства</div>
          </section>

          <section className="agency-stats" style={{ marginTop: 12 }}>
            <article className="agency-stat-card">
              <div className="agency-stat-label">Агентства</div>
              <div className="agency-stat-value">{kpis.total}</div>
            </article>
            <article className="agency-stat-card good">
              <div className="agency-stat-label">Активные</div>
              <div className="agency-stat-value">{kpis.active}</div>
            </article>
            <article className="agency-stat-card bad">
              <div className="agency-stat-label">Приостановленные</div>
              <div className="agency-stat-value">{kpis.suspended}</div>
            </article>
            <article className="agency-stat-card">
              <div className="agency-stat-label">Участники</div>
              <div className="agency-stat-value">{kpis.totalMembers}</div>
            </article>
          </section>

          <div className="agencies-layout" style={{ marginTop: 12 }}>
            <article className="panel agencies-main">
              <div className="panel-head budgets-toolbar">
                <div>
                  <h3>Список агентств</h3>
                  <div className="panel-subtitle">Выберите агентство, чтобы посмотреть детали и настроить доступ.</div>
                </div>
                <button className="ghost-btn" onClick={() => void loadAgencies()} disabled={adminOnly === true}>Обновить</button>
              </div>
              <div className="agencies-cards">
                {agencies.map((agency) => {
                  const active = agency.id === selectedAgencyId;
                  return (
                    <button key={agency.id} className={`agency-card ${active ? "active" : ""}`} onClick={() => setSelectedAgencyId(agency.id)}>
                      <div className="agency-card-head">
                        <div className="agency-name">{agency.name}</div>
                        <span className={`badge ${agency.status === "active" ? "good" : "bad"}`}>{agencyStatusLabel(agency.status)}</span>
                      </div>
                      <div className="agency-meta">
                        <span>Адрес: {agency.slug}</span>
                        <span>Тариф: {agency.plan}</span>
                      </div>
                      <div className="agency-meta">
                        <span>Приглашения клиентов: {agency.allow_client_invites ? "включены" : "отключены"}</span>
                      </div>
                      <div className="agency-meta muted">Обновлено: {fmtDate(agency.updated_at)}</div>
                    </button>
                  );
                })}
                {agencies.length === 0 ? <div className="muted">Агентств пока нет.</div> : null}
              </div>
            </article>

            <aside className="panel agencies-drawer">
              <div className="panel-head">
                <div>
                  <h3>{selectedAgency ? selectedAgency.name : "Данные агентства"}</h3>
                  <div className="panel-subtitle">{selectedAgency ? `${selectedAgency.slug} · ${selectedAgency.plan}` : "Выберите агентство в списке слева"}</div>
                </div>
                <div className="session-controls">
                  <button className="mini-btn" disabled={!selectedAgency || adminOnly === true || !canActivate} onClick={() => void setAgencyStatus("active")}>Активировать</button>
                  <button className="mini-btn" disabled={!selectedAgency || adminOnly === true || !canSuspend} onClick={() => void setAgencyStatus("suspended")}>Приостановить</button>
                  <button
                    className="mini-btn"
                    disabled={!selectedAgency || adminOnly === true}
                    onClick={() => void setClientInvitesAllowed(!selectedAgency?.allow_client_invites)}
                  >
                    {selectedAgency?.allow_client_invites ? "Отключить приглашения клиентов" : "Включить приглашения клиентов"}
                  </button>
                  <button className="mini-btn" disabled={!selectedAgency || adminOnly === true} onClick={() => void deleteAgency()}>Удалить</button>
                </div>
              </div>

              <section className="drawer-kpis">
                <article className="kpi-card">
                  <div className="kpi-title">Участники</div>
                  <div className="kpi-value">{selectedStats.totalMembers}</div>
                </article>
                <article className="kpi-card good">
                  <div className="kpi-title">Активные участники</div>
                  <div className="kpi-value">{selectedStats.activeMembers}</div>
                </article>
              </section>

              <div className="panel drawer-section">
                <h3>Шаг 2. Добавьте участника</h3>
                <div className="panel-subtitle">Выберите пользователя платформы и назначьте его роль внутри агентства.</div>
                <div className="session-controls" style={{ marginTop: 8 }}>
                  <select value={memberUserId} onChange={(e) => setMemberUserId(e.target.value)}>
                    <option value="">Выберите пользователя</option>
                    {users.map((u) => (
                      <option key={u.id} value={u.id}>{u.name} ({userRoleLabel(u.role)})</option>
                    ))}
                  </select>
                  <select value={memberRole} onChange={(e) => setMemberRole(e.target.value as "owner" | "manager" | "member")}>
                    <option value="owner">Владелец</option>
                    <option value="manager">Менеджер</option>
                    <option value="member">Участник</option>
                  </select>
                  <select value={memberStatus} onChange={(e) => setMemberStatus(e.target.value as "active" | "inactive")}>
                    <option value="active">Активен</option>
                    <option value="inactive">Неактивен</option>
                  </select>
                </div>
                <div className="alert-actions" style={{ marginTop: 8 }}>
                  <button className="primary-btn" disabled={!selectedAgency || !memberUserId || adminOnly === true} onClick={() => void upsertMember()}>
                    Сохранить участника
                  </button>
                </div>
                <div className="drawer-list">
                  {members.slice(0, 8).map((m) => {
                    const user = usersById.get(m.user_id);
                    return (
                      <div key={m.id} className="activity-item">
                        <div><strong>{user?.name || m.user_id.slice(0, 8)}</strong></div>
                        <div className="muted">{agencyRoleLabel(m.role)} | {memberStatusLabel(m.status)} | {fmtDate(m.updated_at)}</div>
                        <div className="alert-actions" style={{ marginTop: 6 }}>
                          <button
                            className="mini-btn"
                            disabled={adminOnly === true || user?.role !== "agency" || m.status !== "active"}
                            onClick={() => void openAsAgencyUser(m.user_id)}
                          >
                            Войти от имени
                          </button>
                          <button
                            className="mini-btn"
                            disabled={!canManageMembers || m.status !== "active"}
                            onClick={() => void deactivateMember(m.id)}
                          >
                            Отключить
                          </button>
                          <button className="mini-btn" disabled={!canManageMembers} onClick={() => void removeMember(m.id)}>
                            Удалить
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  {members.length === 0 ? <div className="muted">Участников пока нет.</div> : null}
                </div>
              </div>

              <div className="panel drawer-section">
                <h3>Шаг 3. Пригласите пользователя агентства</h3>
                <div className="panel-subtitle">Создайте одноразовую ссылку: пользователь примет приглашение на странице входа.</div>
                <div className="session-controls" style={{ marginTop: 8 }}>
                  <input
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    placeholder="member@agency.com"
                  />
                </div>
                <div className="alert-actions" style={{ marginTop: 8 }}>
                  <button className="primary-btn" disabled={!selectedAgency || !inviteEmail || !canManageMembers} onClick={() => void issueInvite()}>
                    Создать приглашение
                  </button>
                  <button className="ghost-btn" disabled={!lastInviteUrl} onClick={() => void copyInviteUrl()}>
                    Скопировать последнюю ссылку
                  </button>
                </div>
                {lastInviteUrl ? (
                  <div className="muted" style={{ marginTop: 8, wordBreak: "break-all" }}>
                    Последнее приглашение: {lastInviteUrl}
                  </div>
                ) : null}
                <div className="drawer-list">
                  {invites.slice(0, 8).map((inv) => (
                    <div key={inv.id} className="activity-item">
                      <div><strong>{inv.email}</strong></div>
                      <div className="muted">
                        {agencyRoleLabel(inv.member_role)} | {inviteStatusLabel(inv.status)} | действует до {fmtDate(inv.expires_at)}
                      </div>
                      <div className="alert-actions" style={{ marginTop: 6 }}>
                        <button
                          className="mini-btn"
                          disabled={!canManageMembers || inv.status === "accepted"}
                          onClick={() => void resendInvite(inv.id)}
                        >
                          Отправить повторно
                        </button>
                        <button
                          className="mini-btn"
                          disabled={!canManageMembers || inv.status === "accepted" || inv.status === "expired"}
                          onClick={() => void revokeInvite(inv.id)}
                        >
                          Отозвать
                        </button>
                      </div>
                    </div>
                  ))}
                  {invites.length === 0 ? <div className="muted">Приглашений пока нет.</div> : null}
                </div>
              </div>
            </aside>
          </div>
        </main>
      </div>

      <div className={`modal-backdrop ${createOpen ? "" : "hidden-view"}`} onClick={() => !createLoading && setCreateOpen(false)}>
        <div className="modal-card budgets-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-head">
            <div>
              <h3>Новое агентство</h3>
              <div className="panel-subtitle">Создайте пространство агентства и настройте доступ.</div>
            </div>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Закрыть</button>
          </div>
          <div className="form-grid" style={{ marginTop: 12 }}>
            <label>
              Название
              <input value={createName} onChange={(e) => setCreateName(e.target.value)} placeholder="Название агентства" />
            </label>
            <label>
              Короткий адрес (необязательно)
              <input value={createSlug} onChange={(e) => setCreateSlug(e.target.value)} placeholder="north-star" />
            </label>
            <label>
              Тариф
              <input value={createPlan} onChange={(e) => setCreatePlan(e.target.value)} placeholder="starter" />
            </label>
            <label>
              Приглашения в кабинет клиента
              <select
                value={createAllowClientInvites ? "enabled" : "disabled"}
                onChange={(e) => setCreateAllowClientInvites(e.target.value === "enabled")}
              >
                <option value="enabled">Включены</option>
                <option value="disabled">Отключены</option>
              </select>
            </label>
          </div>
          <div className="modal-actions" style={{ marginTop: 12 }}>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Отмена</button>
            <button className="primary-btn" onClick={() => void createAgency()} disabled={createLoading || !createName.trim()}>
              {createLoading ? "Создаём…" : "Создать"}
            </button>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
