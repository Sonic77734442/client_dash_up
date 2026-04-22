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

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

function agencyRoleLabel(v: "owner" | "manager" | "member") {
  if (v === "owner") return "agency_admin";
  if (v === "manager") return "agency_manager";
  return "agency_member";
}

export default function PlatformAgenciesPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
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
    const usersRes = await req<{ items: UserItem[] }>("/auth/internal/users");
    setUsers((usersRes.items || []).filter((u) => u.role === "agency" || u.role === "admin"));
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
      const [m, i] = await Promise.all([
        req<AgencyMemberOut[]>(`/platform/agencies/${agencyId}/members`),
        req<AgencyInviteOut[]>(`/platform/agencies/${agencyId}/invites?status=all`),
      ]);
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
      setWarning(err instanceof Error ? err.message : "Failed to load platform admin data");
    });
  }, [ready, reloadAll]);

  useEffect(() => {
    if (!selectedAgencyId) {
      setMembers([]);
      setInvites([]);
      return;
    }
    void loadAgencyDetails(selectedAgencyId).catch((err) => {
      setWarning(err instanceof Error ? err.message : "Failed to load agency details");
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
      push("Agency name is required", "error");
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
        }),
      });
      setCreateOpen(false);
      setCreateName("");
      setCreateSlug("");
      setCreatePlan("starter");
      await loadAgencies();
      setSelectedAgencyId(created.id);
      push("Agency created", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Create agency failed", "error");
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
      push(`Agency ${status}`, "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Update agency failed", "error");
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
      push("Member updated", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Member update failed", "error");
    }
  }

  async function issueInvite() {
    if (!selectedAgency || !inviteEmail.trim()) {
      push("Invite email is required", "error");
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
      push("Invite issued", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Issue invite failed", "error");
    }
  }

  async function copyInviteUrl() {
    if (!lastInviteUrl) return;
    try {
      await navigator.clipboard.writeText(lastInviteUrl);
      push("Invite link copied", "success");
    } catch {
      push("Copy failed", "error");
    }
  }

  async function deactivateMember(memberId: string) {
    if (!selectedAgency) return;
    try {
      await req<AgencyMemberOut>(`/platform/agencies/${selectedAgency.id}/members/${memberId}/deactivate`, {
        method: "POST",
      });
      await loadAgencyDetails(selectedAgency.id);
      push("Member deactivated", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Deactivate failed", "error");
    }
  }

  async function removeMember(memberId: string) {
    if (!selectedAgency) return;
    try {
      await req<{ status: string }>(`/platform/agencies/${selectedAgency.id}/members/${memberId}`, {
        method: "DELETE",
      });
      await loadAgencyDetails(selectedAgency.id);
      push("Member removed", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Remove failed", "error");
    }
  }

  async function revokeInvite(inviteId: string) {
    if (!selectedAgency) return;
    try {
      await req<AgencyInviteOut>(`/platform/agencies/${selectedAgency.id}/invites/${inviteId}/revoke`, {
        method: "POST",
      });
      await loadAgencyDetails(selectedAgency.id);
      push("Invite revoked", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Revoke invite failed", "error");
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
      push("Invite resent", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Resend invite failed", "error");
    }
  }

  const adminOnly = ctx && ctx.role !== "admin";
  const canActivate = selectedAgency?.status === "suspended";
  const canSuspend = selectedAgency?.status === "active";

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="platform_admin" subtitle="Internal Admin" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="platform_admin" />
              <div className="topbar-title">Platform Admin: Agencies</div>
              <div className="panel-subtitle">Provision agencies, attach members, and grant tenant access.</div>
            </div>
            <div className="session-controls">
              <a className="ghost-btn" href="/platform/users">Users</a>
              {tokenLoginEnabled ? (
                <>
                  <input
                    type="text"
                    value={session.apiBase}
                    onChange={(e) => setSession((s) => ({ ...s, apiBase: e.target.value }))}
                    placeholder="API base"
                  />
                  <input
                    type="password"
                    value={session.token}
                    onChange={(e) => setSession((s) => ({ ...s, token: e.target.value }))}
                    placeholder="Session token"
                  />
                  <button
                    className="ghost-btn"
                    onClick={async () => {
                      const next = { apiBase: session.apiBase.trim().replace(/\/$/, "") || defaultApiBase, token: session.token.trim() };
                      persist(next);
                      setSession(next);
                      try {
                        await reloadAll();
                        push("Session saved", "success");
                      } catch (err) {
                        setWarning(err instanceof Error ? err.message : "Load failed");
                      }
                    }}
                    disabled={!ready}
                  >
                    Save
                  </button>
                </>
              ) : null}
              <button className="primary-btn" onClick={() => setCreateOpen(true)} disabled={adminOnly === true}>Create Agency</button>
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>
          {adminOnly ? <div className="warning" style={{ marginTop: 10 }}>This screen is internal/admin-only.</div> : null}

          <section className="agency-flow" style={{ marginTop: 12 }}>
            <div className="agency-flow-step">1. Select agency</div>
            <div className="agency-flow-step">2. Add member and role</div>
            <div className="agency-flow-step">3. Invite agency user</div>
          </section>

          <section className="agency-stats" style={{ marginTop: 12 }}>
            <article className="agency-stat-card">
              <div className="agency-stat-label">Agencies</div>
              <div className="agency-stat-value">{kpis.total}</div>
            </article>
            <article className="agency-stat-card good">
              <div className="agency-stat-label">Active</div>
              <div className="agency-stat-value">{kpis.active}</div>
            </article>
            <article className="agency-stat-card bad">
              <div className="agency-stat-label">Suspended</div>
              <div className="agency-stat-value">{kpis.suspended}</div>
            </article>
            <article className="agency-stat-card">
              <div className="agency-stat-label">Members</div>
              <div className="agency-stat-value">{kpis.totalMembers}</div>
            </article>
          </section>

          <div className="agencies-layout" style={{ marginTop: 12 }}>
            <article className="panel agencies-main">
              <div className="panel-head budgets-toolbar">
                <div>
                  <h3>Agencies Registry</h3>
                  <div className="panel-subtitle">Select an agency to open details and manage access.</div>
                </div>
                <button className="ghost-btn" onClick={() => void loadAgencies()} disabled={adminOnly === true}>Refresh</button>
              </div>
              <div className="agencies-cards">
                {agencies.map((agency) => {
                  const active = agency.id === selectedAgencyId;
                  return (
                    <button key={agency.id} className={`agency-card ${active ? "active" : ""}`} onClick={() => setSelectedAgencyId(agency.id)}>
                      <div className="agency-card-head">
                        <div className="agency-name">{agency.name}</div>
                        <span className={`badge ${agency.status === "active" ? "good" : "bad"}`}>{agency.status}</span>
                      </div>
                      <div className="agency-meta">
                        <span>Slug: {agency.slug}</span>
                        <span>Plan: {agency.plan}</span>
                      </div>
                      <div className="agency-meta muted">Updated: {fmtDate(agency.updated_at)}</div>
                    </button>
                  );
                })}
                {agencies.length === 0 ? <div className="muted">No agencies yet.</div> : null}
              </div>
            </article>

            <aside className="panel agencies-drawer">
              <div className="panel-head">
                <div>
                  <h3>{selectedAgency ? selectedAgency.name : "Agency Details"}</h3>
                  <div className="panel-subtitle">{selectedAgency ? `${selectedAgency.slug} · ${selectedAgency.plan}` : "Select agency from left list"}</div>
                </div>
                <div className="session-controls">
                  <button className="mini-btn" disabled={!selectedAgency || adminOnly === true || !canActivate} onClick={() => void setAgencyStatus("active")}>Activate</button>
                  <button className="mini-btn" disabled={!selectedAgency || adminOnly === true || !canSuspend} onClick={() => void setAgencyStatus("suspended")}>Suspend</button>
                </div>
              </div>

              <section className="drawer-kpis">
                <article className="kpi-card">
                  <div className="kpi-title">Members</div>
                  <div className="kpi-value">{selectedStats.totalMembers}</div>
                </article>
                <article className="kpi-card good">
                  <div className="kpi-title">Active Members</div>
                  <div className="kpi-value">{selectedStats.activeMembers}</div>
                </article>
              </section>

              <div className="panel drawer-section">
                <h3>Step 2: Add Member</h3>
                <div className="panel-subtitle">User from `agency/admin` roles, plus access role inside agency.</div>
                <div className="session-controls" style={{ marginTop: 8 }}>
                  <select value={memberUserId} onChange={(e) => setMemberUserId(e.target.value)}>
                    <option value="">Select user</option>
                    {users.map((u) => (
                      <option key={u.id} value={u.id}>{u.name} ({u.role})</option>
                    ))}
                  </select>
                  <select value={memberRole} onChange={(e) => setMemberRole(e.target.value as "owner" | "manager" | "member")}>
                    <option value="owner">agency_admin</option>
                    <option value="manager">agency_manager</option>
                    <option value="member">agency_member</option>
                  </select>
                  <select value={memberStatus} onChange={(e) => setMemberStatus(e.target.value as "active" | "inactive")}>
                    <option value="active">active</option>
                    <option value="inactive">inactive</option>
                  </select>
                </div>
                <div className="alert-actions" style={{ marginTop: 8 }}>
                  <button className="primary-btn" disabled={!selectedAgency || !memberUserId || adminOnly === true} onClick={() => void upsertMember()}>
                    Save Member
                  </button>
                </div>
                <div className="drawer-list">
                  {members.slice(0, 8).map((m) => {
                    const user = usersById.get(m.user_id);
                    return (
                      <div key={m.id} className="activity-item">
                        <div><strong>{user?.name || m.user_id.slice(0, 8)}</strong></div>
                        <div className="muted">{agencyRoleLabel(m.role)} | {m.status} | {fmtDate(m.updated_at)}</div>
                        <div className="alert-actions" style={{ marginTop: 6 }}>
                          <button
                            className="mini-btn"
                            disabled={adminOnly === true || m.status !== "active"}
                            onClick={() => void deactivateMember(m.id)}
                          >
                            Deactivate
                          </button>
                          <button className="mini-btn" disabled={adminOnly === true} onClick={() => void removeMember(m.id)}>
                            Remove
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  {members.length === 0 ? <div className="muted">No members yet.</div> : null}
                </div>
              </div>

              <div className="panel drawer-section">
                <h3>Step 3: Invite Agency User</h3>
                <div className="panel-subtitle">Issue one-time invite link. User accepts invite on login page.</div>
                <div className="session-controls" style={{ marginTop: 8 }}>
                  <input
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    placeholder="member@agency.com"
                  />
                </div>
                <div className="alert-actions" style={{ marginTop: 8 }}>
                  <button className="primary-btn" disabled={!selectedAgency || !inviteEmail || adminOnly === true} onClick={() => void issueInvite()}>
                    Issue Invite
                  </button>
                  <button className="ghost-btn" disabled={!lastInviteUrl} onClick={() => void copyInviteUrl()}>
                    Copy Last Link
                  </button>
                </div>
                {lastInviteUrl ? (
                  <div className="muted" style={{ marginTop: 8, wordBreak: "break-all" }}>
                    Last invite: {lastInviteUrl}
                  </div>
                ) : null}
                <div className="drawer-list">
                  {invites.slice(0, 8).map((inv) => (
                    <div key={inv.id} className="activity-item">
                      <div><strong>{inv.email}</strong></div>
                      <div className="muted">
                        {agencyRoleLabel(inv.member_role)} | {inv.status} | exp {fmtDate(inv.expires_at)}
                      </div>
                      <div className="alert-actions" style={{ marginTop: 6 }}>
                        <button
                          className="mini-btn"
                          disabled={adminOnly === true || inv.status === "accepted"}
                          onClick={() => void resendInvite(inv.id)}
                        >
                          Resend
                        </button>
                        <button
                          className="mini-btn"
                          disabled={adminOnly === true || inv.status === "accepted" || inv.status === "expired"}
                          onClick={() => void revokeInvite(inv.id)}
                        >
                          Revoke
                        </button>
                      </div>
                    </div>
                  ))}
                  {invites.length === 0 ? <div className="muted">No invites yet.</div> : null}
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
              <h3>Create Agency</h3>
              <div className="panel-subtitle">Internal provisioning object for agency access management.</div>
            </div>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Close</button>
          </div>
          <div className="form-grid" style={{ marginTop: 12 }}>
            <label>
              Name
              <input value={createName} onChange={(e) => setCreateName(e.target.value)} placeholder="North Star Agency" />
            </label>
            <label>
              Slug (optional)
              <input value={createSlug} onChange={(e) => setCreateSlug(e.target.value)} placeholder="north-star" />
            </label>
            <label>
              Plan
              <input value={createPlan} onChange={(e) => setCreatePlan(e.target.value)} placeholder="starter" />
            </label>
          </div>
          <div className="modal-actions" style={{ marginTop: 12 }}>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Cancel</button>
            <button className="primary-btn" onClick={() => void createAgency()} disabled={createLoading || !createName.trim()}>
              {createLoading ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
