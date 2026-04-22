"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import { ClientOut } from "../../lib/types";

type ClientStatus = "active" | "inactive" | "archived" | "all";

type ClientForm = {
  name: string;
  legal_name: string;
  status: "active" | "inactive" | "archived";
  default_currency: string;
  timezone: string;
  notes: string;
  invite_email: string;
  invite_expires_in_days: number;
};

type ClientInvite = {
  id: string;
  client_id: string;
  email: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  created_at: string;
  updated_at: string;
};

type ClientInviteIssueResponse = {
  invite: ClientInvite;
  invite_token: string;
  accept_url: string;
};

function emptyForm(): ClientForm {
  return {
    name: "",
    legal_name: "",
    status: "active",
    default_currency: "USD",
    timezone: "UTC",
    notes: "",
    invite_email: "",
    invite_expires_in_days: 7,
  };
}

function fmtDate(v?: string) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

export default function ClientsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [items, setItems] = useState<ClientOut[]>([]);
  const [statusFilter, setStatusFilter] = useState<ClientStatus>("all");
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<"name" | "status" | "updated_at">("updated_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [rowsPerPage, setRowsPerPage] = useState(10);

  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ClientForm>(emptyForm());

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadClients = useCallback(async () => {
    const all = await req<{ items: ClientOut[] }>("/clients?status=all");
    setItems(all.items || []);
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadClients().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load clients"));
  }, [ready, loadClients]);

  const kpis = useMemo(() => {
    const active = items.filter((x) => x.status === "active").length;
    const inactive = items.filter((x) => x.status === "inactive").length;
    const archived = items.filter((x) => x.status === "archived").length;
    return { total: items.length, active, inactive, archived };
  }, [items]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = items
      .filter((x) => (statusFilter === "all" ? true : x.status === statusFilter))
      .filter((x) => {
        if (!q) return true;
        const hay = `${x.name || ""} ${x.id || ""} ${x.legal_name || ""} ${x.notes || ""}`.toLowerCase();
        return hay.includes(q);
      });

    const mul = sortDir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      if (sortBy === "name") {
        const av = String(a.name || "").toLowerCase();
        const bv = String(b.name || "").toLowerCase();
        if (av < bv) return -1 * mul;
        if (av > bv) return 1 * mul;
        return 0;
      }
      if (sortBy === "status") {
        const rank: Record<string, number> = { active: 3, inactive: 2, archived: 1 };
        const av = rank[a.status || "active"] || 0;
        const bv = rank[b.status || "active"] || 0;
        if (av < bv) return -1 * mul;
        if (av > bv) return 1 * mul;
        return 0;
      }
      const av = new Date(a.updated_at || 0).getTime();
      const bv = new Date(b.updated_at || 0).getTime();
      if (av < bv) return -1 * mul;
      if (av > bv) return 1 * mul;
      return 0;
    });
    return rows;
  }, [items, statusFilter, search, sortBy, sortDir]);

  const pages = Math.max(1, Math.ceil(filtered.length / rowsPerPage));
  const safePage = Math.max(1, Math.min(page, pages));
  const pageRows = useMemo(() => {
    const start = (safePage - 1) * rowsPerPage;
    return filtered.slice(start, start + rowsPerPage);
  }, [filtered, safePage, rowsPerPage]);

  useEffect(() => {
    setPage((p) => Math.max(1, Math.min(p, pages)));
  }, [pages]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, search, sortBy, sortDir, rowsPerPage]);

  function toggleSort(next: "name" | "status" | "updated_at") {
    if (sortBy === next) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(next);
      setSortDir(next === "updated_at" ? "desc" : "asc");
    }
  }

  function openCreate() {
    setEditingId(null);
    setForm(emptyForm());
    setModalError("");
    setModalOpen(true);
  }

  function openEdit(row: ClientOut) {
    setEditingId(row.id);
    setForm({
      name: row.name || "",
      legal_name: row.legal_name || "",
      status: (row.status as "active" | "inactive" | "archived") || "active",
      default_currency: row.default_currency || "USD",
      timezone: row.timezone || "UTC",
      notes: row.notes || "",
      invite_email: "",
      invite_expires_in_days: 7,
    });
    setModalError("");
    setModalOpen(true);
  }

  async function saveClient() {
    if (!form.name.trim()) {
      setModalError("Name is required.");
      return;
    }
    try {
      setModalLoading(true);
      setModalError("");
      const payload = {
        name: form.name.trim(),
        legal_name: form.legal_name.trim() || null,
        status: form.status,
        default_currency: form.default_currency.trim().toUpperCase() || "USD",
        timezone: form.timezone.trim() || null,
        notes: form.notes.trim() || null,
      };
      if (editingId) {
        await req<ClientOut>(`/clients/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        push("Client updated", "success");
      } else {
        const created = await req<ClientOut>("/clients", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        const inviteEmail = form.invite_email.trim().toLowerCase();
        if (inviteEmail) {
          const issued = await req<ClientInviteIssueResponse>(`/clients/${created.id}/invites`, {
            method: "POST",
            body: JSON.stringify({
              email: inviteEmail,
              expires_in_days: Number(form.invite_expires_in_days) || 7,
            }),
          });
          try {
            await navigator.clipboard.writeText(issued.accept_url);
            push("Client created, invite issued, link copied", "success");
          } catch {
            push("Client created, invite issued", "success");
          }
        } else {
          push("Client created", "success");
        }
      }
      setModalOpen(false);
      await loadClients();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Save client failed";
      setModalError(msg);
      push(msg, "error");
    } finally {
      setModalLoading(false);
    }
  }

  async function archiveClient(row: ClientOut) {
    try {
      await req<{ status: string }>(`/clients/${row.id}`, { method: "DELETE" });
      push("Client archived", "success");
      await loadClients();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Archive failed";
      setWarning(msg);
      push(msg, "error");
    }
  }

  async function restoreClient(row: ClientOut) {
    try {
      await req<ClientOut>(`/clients/${row.id}`, { method: "PATCH", body: JSON.stringify({ status: "active" }) });
      push("Client restored", "success");
      await loadClients();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Restore failed";
      setWarning(msg);
      push(msg, "error");
    }
  }

  async function copyInviteLink(row: ClientOut) {
    try {
      const invitesRes = await req<ClientInvite[]>(`/clients/${row.id}/invites?status=all`);
      const latest = (invitesRes || [])[0];
      let issued: ClientInviteIssueResponse;
      if (latest) {
        issued = await req<ClientInviteIssueResponse>(`/clients/${row.id}/invites/${latest.id}/resend`, {
          method: "POST",
          body: JSON.stringify({ expires_in_days: 7 }),
        });
      } else {
        const email = window.prompt("Client email for invite");
        const normEmail = String(email || "").trim().toLowerCase();
        if (!normEmail) {
          push("Invite email is required", "info");
          return;
        }
        issued = await req<ClientInviteIssueResponse>(`/clients/${row.id}/invites`, {
          method: "POST",
          body: JSON.stringify({ email: normEmail, expires_in_days: 7 }),
        });
      }
      await navigator.clipboard.writeText(issued.accept_url);
      push("Invite link copied", "success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Invite action failed";
      push(msg, "error");
    }
  }

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="clients" subtitle="Client Registry" />

        <main className="content">
          <header className="topbar">
            <div className="topbar-left">
              <AppTopTabs active="clients" />
              <div className="topbar-title">Client Operations</div>
              <div className="panel-subtitle">Manage client entities before account and budget operations.</div>
            </div>
            <div className="session-controls">
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
                        await loadClients();
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
              <button className="primary-btn" onClick={openCreate}>Create Client</button>
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="kpi-grid" style={{ marginTop: 12 }}>
            <article className="kpi-card">
              <div className="kpi-title">Total Clients</div>
              <div className="kpi-value">{kpis.total}</div>
            </article>
            <article className="kpi-card good">
              <div className="kpi-title">Active</div>
              <div className="kpi-value">{kpis.active}</div>
            </article>
            <article className="kpi-card warn">
              <div className="kpi-title">Inactive</div>
              <div className="kpi-value">{kpis.inactive}</div>
            </article>
            <article className="kpi-card bad">
              <div className="kpi-title">Archived</div>
              <div className="kpi-value">{kpis.archived}</div>
            </article>
          </section>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-head budgets-toolbar">
              <div className="session-controls budgets-filters">
                <label>
                  Status
                  <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as ClientStatus)}>
                    <option value="all">All</option>
                    <option value="active">Active</option>
                    <option value="inactive">Inactive</option>
                    <option value="archived">Archived</option>
                  </select>
                </label>
              </div>
              <div className="session-controls">
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search client / ID" />
                <button className="ghost-btn" onClick={() => void loadClients()}>Refresh</button>
              </div>
            </div>

            <div className="budgets-table-wrap">
              <table className="budgets-table">
                <thead>
                  <tr>
                    <th className={`sortable ${sortBy === "name" ? "active" : ""}`} onClick={() => toggleSort("name")}>
                      Name {sortBy === "name" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </th>
                    <th>Legal Name</th>
                    <th className={`sortable ${sortBy === "status" ? "active" : ""}`} onClick={() => toggleSort("status")}>
                      Status {sortBy === "status" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </th>
                    <th>Currency</th>
                    <th>Timezone</th>
                    <th className={`sortable ${sortBy === "updated_at" ? "active" : ""}`} onClick={() => toggleSort("updated_at")}>
                      Updated {sortBy === "updated_at" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <div className="client-cell">
                          <div className="client-name">{c.name}</div>
                          <div className="client-id">ID: {c.id.slice(0, 8)}</div>
                        </div>
                      </td>
                      <td>{c.legal_name || "--"}</td>
                      <td>{c.status || "active"}</td>
                      <td>{c.default_currency || "USD"}</td>
                      <td>{c.timezone || "--"}</td>
                      <td>{fmtDate(c.updated_at)}</td>
                      <td>
                        <div className="alert-actions" style={{ marginTop: 0 }}>
                          <Link className="mini-btn" href={`/client/${c.id}`}>Open Client</Link>
                          <button className="mini-btn" onClick={() => void copyInviteLink(c)}>Copy Invite Link</button>
                          <button className="mini-btn" onClick={() => openEdit(c)}>Edit</button>
                          {c.status === "archived" ? (
                            <button className="mini-btn" onClick={() => void restoreClient(c)}>Restore</button>
                          ) : (
                            <button
                              className="mini-btn"
                              onClick={() => {
                                if (window.confirm(`Archive client ${c.name}?`)) void archiveClient(c);
                              }}
                            >
                              Archive
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {!filtered.length ? <div className="muted-note" style={{ marginTop: 10 }}>No clients found for current filter.</div> : null}
            <div className="table-footer">
              <div className="session-controls">
                <span className="muted-note">Rows per page</span>
                <select value={String(rowsPerPage)} onChange={(e) => setRowsPerPage(Number(e.target.value))}>
                  <option value="5">5</option>
                  <option value="10">10</option>
                  <option value="20">20</option>
                </select>
                <span className="muted-note">
                  Showing {filtered.length ? (safePage - 1) * rowsPerPage + 1 : 0}-{Math.min(safePage * rowsPerPage, filtered.length)} of {filtered.length}
                </span>
              </div>
              <div className="pager">
                <button className="pager-btn" onClick={() => setPage((p) => Math.max(1, p - 1))}>‹</button>
                <span className="pager-page">{safePage}</span>
                <button className="pager-btn" onClick={() => setPage((p) => Math.min(pages, p + 1))}>›</button>
              </div>
            </div>
          </section>
        </main>
      </div>

      <div className={`modal-backdrop ${modalOpen ? "" : "hidden-view"}`} onClick={() => !modalLoading && setModalOpen(false)}>
        <div className="modal-card budgets-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-head">
            <div>
              <h3 style={{ margin: 0 }}>{editingId ? "Edit Client" : "Create Client"}</h3>
              <div className="panel-subtitle">Client profile used by budgets and account ownership.</div>
            </div>
            <button className="ghost-btn" onClick={() => setModalOpen(false)} disabled={modalLoading}>Close</button>
          </div>

          <div className={`warning ${modalError ? "" : "hidden"}`} style={{ marginTop: 10 }}>{modalError}</div>

          <div className="detail-grid" style={{ marginTop: 10 }}>
            <label>
              Name
              <input value={form.name} onChange={(e) => setForm((s) => ({ ...s, name: e.target.value }))} />
            </label>
            <label>
              Legal Name
              <input value={form.legal_name} onChange={(e) => setForm((s) => ({ ...s, legal_name: e.target.value }))} />
            </label>
            <label>
              Status
              <select value={form.status} onChange={(e) => setForm((s) => ({ ...s, status: e.target.value as ClientForm["status"] }))}>
                <option value="active">active</option>
                <option value="inactive">inactive</option>
                <option value="archived">archived</option>
              </select>
            </label>
            <label>
              Default Currency
              <input value={form.default_currency} onChange={(e) => setForm((s) => ({ ...s, default_currency: e.target.value.toUpperCase() }))} />
            </label>
            <label>
              Timezone
              <input value={form.timezone} onChange={(e) => setForm((s) => ({ ...s, timezone: e.target.value }))} />
            </label>
          </div>

          <label style={{ display: "block", marginTop: 10 }}>
            Notes
            <textarea value={form.notes} onChange={(e) => setForm((s) => ({ ...s, notes: e.target.value }))} rows={3} style={{ width: "100%" }} />
          </label>

          {!editingId ? (
            <div className="detail-grid" style={{ marginTop: 10 }}>
              <label>
                Client login email (optional)
                <input
                  type="email"
                  value={form.invite_email}
                  onChange={(e) => setForm((s) => ({ ...s, invite_email: e.target.value }))}
                  placeholder="client@company.com"
                />
              </label>
              <label>
                Invite expires (days)
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={form.invite_expires_in_days}
                  onChange={(e) => setForm((s) => ({ ...s, invite_expires_in_days: Number(e.target.value) || 7 }))}
                />
              </label>
            </div>
          ) : null}

          <div className="session-controls" style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <button className="ghost-btn" onClick={() => setModalOpen(false)} disabled={modalLoading}>Cancel</button>
            <button className="primary-btn" onClick={() => void saveClient()} disabled={modalLoading || !form.name.trim()}>
              {modalLoading ? "Saving..." : editingId ? "Save Changes" : "Create Client"}
            </button>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
