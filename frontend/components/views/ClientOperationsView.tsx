"use client";

import { Client, ClientOpsRow, OperationalAction } from "../../lib/types";

const actionLabels: Record<string, string> = {
  scale: "Масштабирование",
  cap: "Ограничение расхода",
  pause: "Приостановка",
  review: "Проверка",
};

const statusLabels: Record<string, string> = {
  queued: "В очереди",
  pending: "Ожидает",
  running: "В работе",
  applied: "Применено",
  completed: "Выполнено",
  succeeded: "Выполнено",
  success: "Выполнено",
  failed: "Ошибка",
  cancelled: "Отменено",
};

const paceLabels: Record<ClientOpsRow["pace"], string> = {
  critical: "Критический",
  warning: "Требует внимания",
  stable: "В норме",
  no_budget: "Нет бюджета",
};

function actionLabel(value: string | null | undefined) {
  const key = String(value || "").trim().toLowerCase();
  return actionLabels[key] || value || "Действие";
}

function statusLabel(value: string | null | undefined) {
  const key = String(value || "").trim().toLowerCase();
  return statusLabels[key] || value || "Статус не указан";
}

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
  fmtMoney: (v: number | null | undefined, currency?: string) => string;
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
  const rows = filteredClientOpsRows;
  const activeClients = rows.length;
  const totalSpend = rows.reduce((s, x) => s + Number(x.spend || 0), 0);
  const atRisk = rows.filter((x) => x.riskScore >= 70).length;
  const usageRows = rows.filter((x) => x.usage != null);
  const paceDelta = usageRows.reduce((s, x) => s + (Number(x.usage || 0) - 80), 0) / Math.max(1, usageRows.length);
  const rowCurrencies = new Set(rows.map((row) => row.currency));
  const totalSpendValue =
    rowCurrencies.size === 1
      ? fmtMoney(totalSpend, [...rowCurrencies][0])
      : rowCurrencies.size > 1
      ? "Разные валюты"
      : fmtMoney(0, "USD");

  return (
    <>
      <section className="clientops-kpi-row">
        {[
          { label: "Клиенты в выборке", value: String(activeClients), note: "текущий срез портфеля" },
          { label: "Общий расход", value: totalSpendValue, note: "по выбранным клиентам" },
          { label: "Требуют внимания", value: String(atRisk), note: "риск 70 и выше" },
          {
            label: "Отклонение темпа",
            value: `${paceDelta >= 0 ? "+" : ""}${paceDelta.toFixed(1)}%`,
            note: "от целевого освоения 80%",
          },
        ].map((c) => (
          <article key={c.label} className="clientops-kpi-card">
            <div className="clientops-kpi-label">{c.label}</div>
            <div className="clientops-kpi-value">{c.value}</div>
            <div className="clientops-kpi-note">{c.note}</div>
          </article>
        ))}
      </section>

      <section className="clientops-controls panel">
        <div className="clientops-controls-row">
          <input
            className="clientops-search"
            placeholder="Найти клиента, ответственного или код"
            value={clientOpsSearch}
            onChange={(e) => setClientOpsSearch(e.target.value)}
          />
          <div className="density-toggle">
            <button className={`density-btn ${density === "comfortable" ? "active" : ""}`} onClick={() => setDensity("comfortable")}>Обычная</button>
            <button className={`density-btn ${density === "compact" ? "active" : ""}`} onClick={() => setDensity("compact")}>Компактная</button>
          </div>
          <button className="ghost-btn" onClick={() => setPage(1)}>Применить</button>
        </div>
        <div className="chip-row">
          {[
            ["all", "Все клиенты"],
            ["at_risk", "В зоне риска"],
            ["overspending", "Перерасход"],
            ["no_budget", "Без бюджета"],
            ["has_alerts", "С предупреждениями"],
          ].map(([k, label]) => (
            <button key={k} className={`chip-btn ${clientOpsChip === k ? "active" : ""}`} onClick={() => { setClientOpsChip(k as typeof clientOpsChip); setPage(1); }}>
              {label}
            </button>
          ))}
        </div>
      </section>

      <section className="clientops-grid">
        <article className="panel clientops-table-panel">
          <div className="panel-head">
            <div>
              <h3>Портфель клиентов</h3>
              <div className="panel-subtitle">Расход, бюджет, темп и уровень риска в одном списке</div>
            </div>
            <div className="muted-note">{filteredClientOpsRows.length} клиентов</div>
          </div>
          <table className={`clientops-table ${density === "compact" ? "compact-density" : ""}`}>
            <thead>
              <tr>
                {[
                  ["name", "Клиент"],
                  ["spend", "Расход"],
                  ["budget", "Бюджет"],
                  ["usage", "Освоение"],
                  ["pace", "Темп"],
                  ["riskScore", "Риск"],
                ].map(([k, label]) => (
                  <th
                    key={k}
                    className={`sortable ${sortBy === k ? "active" : ""}`}
                    aria-sort={sortBy === k ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
                    onClick={() => {
                      if (sortBy === k) setSortDir(sortDir === "asc" ? "desc" : "asc");
                      else {
                        setSortBy(k as typeof sortBy);
                        setSortDir(k === "name" ? "asc" : "desc");
                      }
                    }}
                  >
                    {label}{sortBy === k ? <span aria-hidden="true"> {sortDir === "asc" ? "↑" : "↓"}</span> : null}
                  </th>
                ))}
                <th>Последнее действие</th>
                <th>Ответственный</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {pagedClientOpsRows.map((r) => {
                const usage = r.usage == null ? null : Math.max(0, Math.min(130, r.usage));
                const usageTone = usage == null ? "low" : usage >= 90 ? "high" : usage >= 70 ? "mid" : "low";
                const riskTone = r.riskScore >= 80 ? "high" : r.riskScore >= 60 ? "medium" : "low";
                const lastActionText = r.lastAction
                  ? `${actionLabel(r.lastAction.action)} · ${statusLabel(r.lastAction.status)}`
                  : "—";
                return (
                  <tr key={r.id}>
                    <td>
                      <div className="client-cell">
                        <div className="client-name">{r.name}</div>
                        <div className="client-id">Код: {r.id.slice(0, 8)}</div>
                      </div>
                    </td>
                    <td>{fmtMoney(r.spend, r.currency)}</td>
                    <td>{r.budget ? fmtMoney(r.budget, r.currency) : "—"}</td>
                    <td>
                      <div className={`usage-bar ${usageTone}`}><div style={{ width: `${usage == null ? 0 : Math.min(100, usage)}%` }}></div></div>
                      {usage == null ? "—" : `${usage.toFixed(1)}%`}
                    </td>
                    <td><span className={`badge ${r.pace === "critical" ? "bad" : r.pace === "warning" || r.pace === "no_budget" ? "warn" : "good"}`}>{paceLabels[r.pace]}</span></td>
                    <td><span className={`risk-score ${riskTone}`}>{String(r.riskScore).padStart(2, "0")}</span></td>
                    <td>{lastActionText}</td>
                    <td><span className="owner-pill">{r.owner}</span></td>
                    <td><button className="mini-btn open-client-btn" onClick={() => onOpenClient(r.id)}>Открыть</button></td>
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
                return `Показано ${start}–${end} из ${filteredClientOpsRows.length}`;
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
            <h3>Требуют внимания</h3>
            <div className="panel-subtitle">Сначала клиенты с наибольшим операционным риском</div>
            {filteredClientOpsRows.filter((x) => x.riskScore >= 70).slice(0, 3).map((r) => {
              const highPriority = r.riskScore >= 80;
              return (
                <div key={r.id} className={`alert-card ${highPriority ? "high" : ""}`}>
                  <div className={`alert-priority ${highPriority ? "high" : ""}`}>{highPriority ? "Высокий приоритет" : "Средний приоритет"}</div>
                  <div className="insight-title" style={{ marginTop: 8 }}>{r.name}: нужна проверка</div>
                  <div className="insight-text">Расход {fmtMoney(r.spend, r.currency)} · бюджет {r.budget ? fmtMoney(r.budget, r.currency) : "не задан"}.</div>
                  <div className="alert-actions">
                    <button className="mini-btn" onClick={() => void onAlertAction(r, "cap")}>Создать задачу: ограничить</button>
                    <button className="mini-btn" onClick={() => void onAlertAction(r, "review")}>Создать задачу: проверить</button>
                    <button className="mini-btn open-client-btn" onClick={() => onOpenClient(r.id)}>Открыть</button>
                  </div>
                </div>
              );
            })}
            {!filteredClientOpsRows.some((x) => x.riskScore >= 70) ? <div className="muted-note">В текущей выборке нет срочных отклонений.</div> : null}
          </article>
          <article className="panel">
            <h3>Последние действия</h3>
            <div className="panel-subtitle">Операции агентства по клиентам</div>
            {!recentActions.length ? (
              <div className="muted-note">Действий пока нет.</div>
            ) : (
              recentActions.slice(0, 6).map((x) => {
                const d = new Date(x.created_at);
                const ts = Number.isNaN(d.getTime()) ? x.created_at : d.toLocaleString("ru-RU");
                const action = actionLabel(x.action);
                const client = clients.find((c) => c.id === x.client_id);
                return (
                  <div key={x.id} className="activity-item">
                    <div className="activity-title">{action}{client ? ` · ${client.name}` : ""}</div>
                    <div className="activity-meta">{statusLabel(x.status)} · {ts}</div>
                    <div className="activity-action">
                      {x.client_id ? (
                        <button className="mini-btn open-client-btn" onClick={() => onOpenClient(x.client_id || "")}>Открыть</button>
                      ) : (
                        <button className="mini-btn" disabled>Нет привязки к клиенту</button>
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
