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
  role: "admin" | "agency" | "client";
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

function roleLabel(role: "admin" | "agency" | "client") {
  if (role === "client") return "solo_client";
  return role;
}

export default function PlatformUsersPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [users, setUsers] = useState<UserItem[]>([]);
  const [search, setSearch] = useState("");
  const [createEmail, setCreateEmail] = useState("");
  const [createName, setCreateName] = useState("");
  const [createRole, setCreateRole] = useState<"admin" | "agency" | "client">("client");
  const [createStatus, setCreateStatus] = useState<"active" | "inactive">("active");

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadUsers = useCallback(async () => {
    await req<AuthMeResponse>("/auth/me");
    const rows = await req<{ items: UserItem[] }>("/auth/internal/users");
    setUsers(rows.items || []);
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadUsers().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load users"));
  }, [ready, loadUsers]);

  async function createUser() {
    if (!createName.trim()) {
      push("Name is required", "error");
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
      push("User created", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Create user failed", "error");
    }
  }

  async function patchUser(userId: string, patch: Partial<Pick<UserItem, "role" | "status">>) {
    try {
      await req<UserItem>(`/auth/internal/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      await loadUsers();
      push("User updated", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "User update failed", "error");
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
        <AppSidebar active="platform_admin" subtitle="Platform Administration" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="platform_admin" />
              <div className="topbar-title">Platform Users</div>
            </div>
            <div className="session-controls">
              <a className="ghost-btn" href="/platform/agencies">Go To Agencies</a>
              <a className="ghost-btn" href="/platform/alerts">Go To Alerts</a>
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
                    await loadUsers();
                    push("Session saved", "success");
                  }}
                >
                  Save
                </button>
                </>
              ) : null}
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head">
              <div>
                <h3 style={{ margin: 0 }}>Create User</h3>
                <div className="panel-subtitle">Create platform user with global role.</div>
              </div>
            </div>
            <div className="session-controls" style={{ marginTop: 10 }}>
              <input type="email" value={createEmail} onChange={(e) => setCreateEmail(e.target.value)} placeholder="email@company.com (optional)" />
              <input value={createName} onChange={(e) => setCreateName(e.target.value)} placeholder="Full name" />
              <select value={createRole} onChange={(e) => setCreateRole(e.target.value as "admin" | "agency" | "client")}>
                <option value="client">solo_client</option>
                <option value="agency">agency</option>
                <option value="admin">admin</option>
              </select>
              <select value={createStatus} onChange={(e) => setCreateStatus(e.target.value as "active" | "inactive")}>
                <option value="active">active</option>
                <option value="inactive">inactive</option>
              </select>
              <button className="primary-btn" onClick={() => void createUser()}>Create</button>
            </div>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="chip-row" style={{ marginTop: 0 }}>
              <input className="clientops-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name/email/role/status" />
              <button className="ghost-btn" onClick={() => void loadUsers()}>Refresh</button>
            </div>
            <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
              <table className="budgets-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Updated</th>
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
                          <option value="client">{roleLabel("client")}</option>
                        </select>
                      </td>
                      <td>
                        <select value={u.status} onChange={(e) => void patchUser(u.id, { status: e.target.value as UserItem["status"] })}>
                          <option value="active">active</option>
                          <option value="inactive">inactive</option>
                        </select>
                      </td>
                      <td>{fmtDate(u.updated_at || u.created_at)}</td>
                    </tr>
                  ))}
                  {!filtered.length ? (
                    <tr>
                      <td colSpan={5} className="muted-note">No users.</td>
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
