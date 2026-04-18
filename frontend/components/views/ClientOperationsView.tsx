"use client";

import { Client, ClientOpsRow, OperationalAction } from "../../lib/types";

type ClientOperationsViewProps = {
  clientOpsRows: ClientOpsRow[];
  filteredClientOpsRows: ClientOpsRow[];
  pagedClientOpsRows: ClientOpsRow[];
  clients: Client[];
  recentActions: OperationalAction[];
  clientOpsSearch: string;
  setClientOpsSearch: (v: string) => void;
  clientOpsChip: "all" | "at_risk" | "overspending" | "no_budget" | "has_alerts";
  setClientOpsChip: (v: "all" | "at_risk" | "overspending" | "no_budget" | "has_alerts") => void;
  density: "comfortable" | "compact";
  setDensity: (v: "comfortable" | "compact") => void;
  sortBy: "name" | "spend" | "budget" | "usage" | "pace" | "riskScore";
  sortDir: "asc" | "desc";
  setSortBy: (v: "name" | "spend" | "budget" | "usage" | "pace" | "riskScore") => void;
  setSortDir: (v: "asc" | "desc") => void;
  page: number;
  pages: number;
  pageSize: number;
  setPage: (v: number | ((p: number) => number)) => void;
  onOpenClient: (clientId: string) => void;
  onAlertAction: (row: ClientOpsRow, action: "cap" | "review") => Promise<void>;
  fmtMoney: (v: number | null | undefined) => string;
};

export function ClientOperationsView({
  clientOpsRows,
  filteredClientOpsRows,
  pagedClientOpsRows,
  clients,
  recentActions,
  clientOpsSearch,
  setClientOpsSearch,
  clientOpsChip,
  setClientOpsChip,
  density,
  setDensity,
  sortBy,
  sortDir,
  setSortBy,
  setSortDir,
  page,
  pages,
  pageSize,
  setPage,
  onOpenClient,
  onAlertAction,
  fmtMoney,
}: ClientOperationsViewProps) {
  const rows = filteredClientOpsRows.length ? filteredClientOpsRows : clientOpsRows;
  const activeClients = rows.length;
  const totalSpend = rows.reduce((s, x) => s + Number(x.spend || 0), 0);
  const atRisk = rows.filter((x) => x.riskScore >= 70).length;
  const usageRows = rows.filter((x) => x.usage != null);
  const paceDelta = usageRows.reduce((s, x) => s + (Number(x.usage || 0) - 80), 0) / Math.max(1, usageRows.length);

  return (
    <>
      <section className="clientops-kpi-row">
        {[
          { label: "Active Clients", value: String(activeClients), note: "+ this period" },
          { label: "Total Spend", value: fmtMoney(totalSpend), note: "portfolio total" },
          { label: "Clients At Risk", value: String(atRisk), note: "requires review", risk: true },
          { label: "Avg Pace Delta", value: `${paceDelta >= 0 ? "+" : ""}${paceDelta.toFixed(1)}%`, note: "vs target usage 80%" },
        ].map((c) => (
          <article key={c.label} className={`clientops-kpi-card ${c.risk ? "risk" : ""}`}>
            <div className="clientops-kpi-label">{c.label}</div>
            <div className="clientops-kpi-value">{c.value}</div>
            <div className="clientops-kpi-note">{c.note}</div>
          </article>
        ))}
      </section>

      <section className="clientops-controls panel">
        <div className="clientops-controls-row">
          <input className="clientops-search" placeholder="Search clients, owners, or IDs..." value={clientOpsSearch} onChange={(e) => setClientOpsSearch(e.target.value)} />
          <div className="density-toggle">
            <button className={`density-btn ${density === "comfortable" ? "active" : ""}`} onClick={() => setDensity("comfortable")}>Comfortable</button>
            <button className={`density-btn ${density === "compact" ? "active" : ""}`} onClick={() => setDensity("compact")}>Compact</button>
          </div>
          <button className="ghost-btn" onClick={() => setPage(1)}>Apply Filters</button>
        </div>
        <div className="chip-row">
          {[
            ["all", "All Clients"],
            ["at_risk", "Only At Risk"],
            ["overspending", "Overspending"],
            ["no_budget", "No Budget"],
            ["has_alerts", "Has Alerts"],
          ].map(([k, label]) => (
            <button key={k} className={`chip-btn ${clientOpsChip === k ? "active" : ""}`} onClick={() => { setClientOpsChip(k as typeof clientOpsChip); setPage(1); }}>
              {label}
            </button>
          ))}
        </div>
      </section>

      <section className="clientops-grid">
        <article className="panel clientops-table-panel">
          <table className={`clientops-table ${density === "compact" ? "compact-density" : ""}`}>
            <thead>
              <tr>
                {[
                  ["name", "Client"],
                  ["spend", "Spend"],
                  ["budget", "Budget"],
                  ["usage", "Usage %"],
                  ["pace", "Pace"],
                  ["riskScore", "Risk Score"],
                ].map(([k, label]) => (
                  <th
                    key={k}
                    className={`sortable ${sortBy === k ? "active" : ""}`}
                    onClick={() => {
                      if (sortBy === k) setSortDir(sortDir === "asc" ? "desc" : "asc");
                      else {
                        setSortBy(k as typeof sortBy);
                        setSortDir(k === "name" ? "asc" : "desc");
                      }
                    }}
                  >
                    {label}
                  </th>
                ))}
                <th>Last Action</th>
                <th>Owner</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pagedClientOpsRows.map((r) => {
                const usage = r.usage == null ? null : Math.max(0, Math.min(130, r.usage));
                const usageTone = usage == null ? "low" : usage >= 90 ? "high" : usage >= 70 ? "mid" : "low";
                const riskTone = r.riskScore >= 80 ? "high" : r.riskScore >= 60 ? "medium" : "low";
                const lastActionText = r.lastAction ? `${String(r.lastAction.action || "").toUpperCase()} ${String(r.lastAction.status || "")}` : "—";
                return (
                  <tr key={r.id}>
                    <td>
                      <div className="client-cell">
                        <div className="client-name">{r.name}</div>
                        <div className="client-id">ID: {r.id.slice(0, 8)}</div>
                      </div>
                    </td>
                    <td>{fmtMoney(r.spend)}</td>
                    <td>{r.budget ? fmtMoney(r.budget) : "—"}</td>
                    <td>
                      <div className={`usage-bar ${usageTone}`}><div style={{ width: `${usage == null ? 0 : Math.min(100, usage)}%` }}></div></div>
                      {usage == null ? "—" : `${usage.toFixed(1)}%`}
                    </td>
                    <td><span className={`badge ${r.pace === "critical" ? "bad" : r.pace === "warning" ? "warn" : "good"}`}>{r.pace.toUpperCase()}</span></td>
                    <td><span className={`risk-score ${riskTone}`}>{String(r.riskScore).padStart(2, "0")}</span></td>
                    <td>{lastActionText}</td>
                    <td><span className="owner-pill">{r.owner}</span></td>
                    <td><button className="mini-btn open-client-btn" onClick={() => onOpenClient(r.id)}>OPEN CLIENT</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="table-footer">
            <div className="muted-note">
              {(() => {
                const start = filteredClientOpsRows.length ? (page - 1) * pageSize + 1 : 0;
                const end = Math.min(page * pageSize, filteredClientOpsRows.length);
                return `Showing ${start}-${end} of ${filteredClientOpsRows.length} clients`;
              })()}
            </div>
            <div className="pager">
              <button className="pager-btn" onClick={() => setPage((p) => Math.max(1, p - 1))}>
                &lt;
              </button>
              <span className="pager-page">{page}</span>
              <button className="pager-btn" onClick={() => setPage((p) => Math.min(pages, p + 1))}>
                &gt;
              </button>
            </div>
          </div>
        </article>

        <div className="side-stack">
          <article className="panel">
            <h3>Urgent Alerts</h3>
            {filteredClientOpsRows.filter((x) => x.riskScore >= 70).slice(0, 3).map((r, idx) => (
              <div key={r.id} className={`alert-card ${idx === 0 ? "high" : ""}`}>
                <div className={`alert-priority ${idx === 0 ? "high" : ""}`}>{idx === 0 ? "HIGH PRIORITY" : "MED PRIORITY"}</div>
                <div className="insight-title" style={{ marginTop: 8 }}>{r.name} needs review</div>
                <div className="insight-text">Spend {fmtMoney(r.spend)} vs budget {r.budget ? fmtMoney(r.budget) : "—"}.</div>
                <div className="alert-actions">
                  <button className="mini-btn" onClick={() => void onAlertAction(r, "cap")}>CAP</button>
                  <button className="mini-btn" onClick={() => void onAlertAction(r, "review")}>REVIEW</button>
                  <button className="mini-btn open-client-btn" onClick={() => onOpenClient(r.id)}>OPEN CLIENT</button>
                </div>
              </div>
            ))}
            {!filteredClientOpsRows.some((x) => x.riskScore >= 70) ? <div className="muted-note">No urgent alerts in current scope.</div> : null}
          </article>
          <article className="panel">
            <h3>Recent Activity</h3>
            {!recentActions.length ? (
              <div className="muted-note">No activity yet.</div>
            ) : (
              recentActions.slice(0, 6).map((x) => {
                const d = new Date(x.created_at);
                const ts = Number.isNaN(d.getTime()) ? x.created_at : d.toLocaleString();
                const action = String(x.action || "").toUpperCase();
                const client = clients.find((c) => c.id === x.client_id);
                return (
                  <div key={x.id} className="activity-item">
                    <div className="activity-title">{action} {client ? `for ${client.name}` : ""}</div>
                    <div className="activity-meta">{x.status?.toUpperCase()} • {ts}</div>
                    <div className="activity-action">
                      {x.client_id ? (
                        <button className="mini-btn open-client-btn" onClick={() => onOpenClient(x.client_id || "")}>OPEN CLIENT</button>
                      ) : (
                        <button className="mini-btn" disabled>NO CLIENT CONTEXT</button>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </article>
        </div>
      </section>
    </>
  );
}
