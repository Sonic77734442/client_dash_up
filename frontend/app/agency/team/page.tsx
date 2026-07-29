"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../../components/AppSidebar";
import { AppTopTabs } from "../../../components/AppTopTabs";
import { ToastHost } from "../../../components/ToastHost";
import { useSession } from "../../../hooks/useSession";
import { useToast } from "../../../hooks/useToast";
import { fetchJson } from "../../../lib/api";
import {
  AgencyInviteIssueResponse,
  AgencyInviteOut,
  AgencyMemberOut,
  AgencyOut,
  AuthUser,
} from "../../../lib/types";

export default function AgencyTeamPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const { session, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();
  const [agency, setAgency] = useState<AgencyOut | null>(null);
  const [members, setMembers] = useState<AgencyMemberOut[]>([]);
  const [invites, setInvites] = useState<AgencyInviteOut[]>([]);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [email, setEmail] = useState("");
  const [memberRole, setMemberRole] = useState<"owner" | "manager" | "member">("member");
  const [warning, setWarning] = useState("");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    try {
      const agencies = await req<{ items: AgencyOut[] }>("/platform/agencies?status=active");
      const currentAgency = agencies.items?.[0] || null;
      setAgency(currentAgency);
      if (!currentAgency) {
        setMembers([]);
        setInvites([]);
        setWarning("Для пользователя не найдено агентство.");
        return;
      }
      const [memberRows, inviteRows] = await Promise.all([
        req<AgencyMemberOut[]>(`/platform/agencies/${currentAgency.id}/members`),
        req<AgencyInviteOut[]>(`/platform/agencies/${currentAgency.id}/invites?status=all`),
      ]);
      setMembers(memberRows || []);
      setInvites(inviteRows || []);
      try {
        const userRows = await req<{ items: AuthUser[] }>("/auth/internal/users");
        setUsers(userRows.items || []);
      } catch {
        setUsers([]);
      }
      setWarning("");
    } catch (error) {
      setWarning(error instanceof Error ? error.message : "Не удалось загрузить команду");
    }
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadData();
  }, [ready, loadData]);

  const usersById = useMemo(() => new Map(users.map((item) => [item.id, item])), [users]);

  async function inviteMember() {
    if (!agency || !email.trim()) {
      push("Введите email сотрудника", "error");
      return;
    }
    try {
      const issued = await req<AgencyInviteIssueResponse>(`/platform/agencies/${agency.id}/invites`, {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), member_role: memberRole, expires_in_days: 7 }),
      });
      setEmail("");
      await loadData();
      push(`Приглашение создано: ${issued.invite.email}`, "success");
    } catch (error) {
      push(error instanceof Error ? error.message : "Не удалось создать приглашение", "error");
    }
  }

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="clients" />
        <main className="content">
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs active="clients" />
              <div className="topbar-title">Команда и доступы</div>
              <div className="panel-subtitle">{agency?.name || "Агентство"} · сотрудники и приглашения</div>
            </div>
            <button className="ghost-btn" onClick={() => void loadData()}>Обновить</button>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="panel access-assign-panel">
            <div>
              <h3>Пригласить сотрудника</h3>
              <div className="panel-subtitle">Доступ появится после принятия приглашения.</div>
            </div>
            <div className="access-assign-controls">
              <label>
                Email
                <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@agency.com" />
              </label>
              <label>
                Роль
                <select value={memberRole} onChange={(event) => setMemberRole(event.target.value as typeof memberRole)}>
                  <option value="member">Сотрудник</option>
                  <option value="manager">Менеджер</option>
                  <option value="owner">Владелец</option>
                </select>
              </label>
              <button className="primary-btn" onClick={() => void inviteMember()} disabled={!agency}>Отправить приглашение</button>
            </div>
          </section>

          <section className="role-dashboard-grid">
            <article className="panel">
              <h3>Участники</h3>
              <div className="budgets-table-wrap">
                <table>
                  <thead><tr><th>Сотрудник</th><th>Роль</th><th>Состояние</th><th>Добавлен</th></tr></thead>
                  <tbody>
                    {members.map((member) => {
                      const user = usersById.get(member.user_id);
                      return (
                        <tr key={member.id}>
                          <td>{user?.name || member.user_id}<div className="muted-note">{user?.email || ""}</div></td>
                          <td>{member.role}</td>
                          <td><span className={`badge ${member.status === "active" ? "good" : "warn"}`}>{member.status}</span></td>
                          <td>{new Date(member.created_at).toLocaleDateString("ru-RU")}</td>
                        </tr>
                      );
                    })}
                    {!members.length ? <tr><td colSpan={4} className="muted-note">Участников пока нет.</td></tr> : null}
                  </tbody>
                </table>
              </div>
            </article>

            <aside className="panel">
              <h3>Приглашения</h3>
              <div className="decision-list">
                {invites.slice(0, 10).map((invite) => (
                  <div className="decision-row settings-row" key={invite.id}>
                    <span className={`decision-dot ${invite.status === "pending" ? "warning" : "info"}`} />
                    <div>
                      <div className="decision-title">{invite.email}</div>
                      <div className="activity-meta">{invite.member_role} · до {new Date(invite.expires_at).toLocaleDateString("ru-RU")}</div>
                    </div>
                    <span className="badge">{invite.status}</span>
                  </div>
                ))}
                {!invites.length ? <div className="muted-note">Нет активных приглашений.</div> : null}
              </div>
            </aside>
          </section>
        </main>
      </div>
      <ToastHost toasts={toasts} />
    </>
  );
}
