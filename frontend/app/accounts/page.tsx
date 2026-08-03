"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { DataSourcesNav } from "../../components/DataSourcesNav";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson } from "../../lib/api";
import { AdAccount, AdAccountSyncJob, ClientOut } from "../../lib/types";

type StatusChip = "all" | "unmapped" | "errors";

type MappingForm = {
  client_id: string;
};

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString("ru-RU");
}

function initials(name: string) {
  return name
    .split(" ")
    .map((x) => x[0] || "")
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function accountSyncStatus(a: AdAccount): "synced" | "unmapped" | "error" {
  const status = String(a.sync_status || "").toLowerCase();
  if (status === "error") return "error";
  if (!a.client_id) return "unmapped";
  return "synced";
}

function accountStatusLabel(status: ReturnType<typeof accountSyncStatus>) {
  if (status === "synced") return "Готов";
  if (status === "unmapped") return "Без клиента";
  return "Ошибка";
}

function requireItems<T>(payload: unknown, label: string): T[] {
  if (!payload || typeof payload !== "object") {
    throw new Error(`${label}: сервис вернул некорректный ответ`);
  }
  const items = (payload as { items?: unknown }).items;
  if (!Array.isArray(items)) {
    throw new Error(`${label}: сервис вернул некорректный список`);
  }
  return items as T[];
}

function sanitizedMetadataForActivation(source?: Record<string, unknown> | null) {
  const next: Record<string, unknown> = { ...(source || {}) };
  delete next.sync_status;
  delete next.sync_error;
  delete next.sync_error_code;
  delete next.sync_error_category;
  delete next.sync_retryable;
  delete next.sync_next_retry_at;
  delete next.sync_attempt;
  delete next.last_sync_job_id;
  return next;
}

export default function AccountsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [syncJobs, setSyncJobs] = useState<AdAccountSyncJob[]>([]);

  const [chip, setChip] = useState<StatusChip>("all");
  const [platform, setPlatform] = useState("all");
  const [clientId, setClientId] = useState("all");
  const [search, setSearch] = useState("");

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState("");

  const [mapOpen, setMapOpen] = useState(false);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapError, setMapError] = useState("");
  const [mappingForm, setMappingForm] = useState<MappingForm>({ client_id: "" });

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const loadData = useCallback(async () => {
    const [acc, cls, jobs] = await Promise.all([
      req<{ items: AdAccount[] }>("/ad-accounts?status=all"),
      req<{ items: ClientOut[] }>("/clients?status=all"),
      req<{ items: AdAccountSyncJob[] }>("/ad-accounts/sync/jobs?status=all&limit=200"),
    ]);
    setAccounts(requireItems<AdAccount>(acc, "Рекламные аккаунты"));
    setClients(requireItems<ClientOut>(cls, "Клиенты"));
    setSyncJobs(requireItems<AdAccountSyncJob>(jobs, "История обновлений"));
  }, [req]);

  useEffect(() => {
    if (!ready) return;
    void loadData().catch((err) =>
      setWarning(err instanceof Error ? err.message : "Не удалось загрузить рекламные аккаунты")
    );
  }, [ready, loadData]);

  const clientNameMap = useMemo(() => new Map(clients.map((c) => [c.id, c.name])), [clients]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return accounts
      .filter((a) => (platform === "all" ? true : a.platform === platform))
      .filter((a) => (clientId === "all" ? true : a.client_id === clientId))
      .filter((a) => {
        const s = accountSyncStatus(a);
        if (chip === "all") return true;
        if (chip === "unmapped") return s === "unmapped";
        return s === "error";
      })
      .filter((a) => {
        if (!q) return true;
        const hay = `${a.name} ${a.external_account_id} ${a.id} ${clientNameMap.get(a.client_id) || ""}`.toLowerCase();
        return hay.includes(q);
      })
      .sort((a, b) => new Date(b.last_sync_at || b.updated_at || 0).getTime() - new Date(a.last_sync_at || a.updated_at || 0).getTime());
  }, [accounts, platform, clientId, chip, search, clientNameMap]);

  useEffect(() => {
    if (!rows.length) {
      setSelectedId("");
      return;
    }
    if (!selectedId || !rows.some((r) => r.id === selectedId)) {
      setSelectedId(rows[0].id);
    }
  }, [rows, selectedId]);

  const selected = useMemo(() => rows.find((x) => x.id === selectedId) || null, [rows, selectedId]);
  const selectedCount = selectedIds.length;

  const kpis = useMemo(() => {
    const total = accounts.length;
    const mapped = accounts.filter((a) => !!a.client_id).length;
    const unmapped = accounts.filter((a) => !a.client_id).length;
    const errors = accounts.filter((a) => accountSyncStatus(a) === "error").length;
    return { total, mapped, unmapped, errors };
  }, [accounts]);

  const platformOptions = useMemo(
    () => ["all", ...Array.from(new Set(accounts.map((a) => a.platform))).sort()],
    [accounts]
  );

  function toggleOne(id: string) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function toggleAllCurrent() {
    const ids = rows.map((r) => r.id);
    const allSelected = ids.length > 0 && ids.every((id) => selectedIds.includes(id));
    setSelectedIds(allSelected ? selectedIds.filter((id) => !ids.includes(id)) : Array.from(new Set([...selectedIds, ...ids])));
  }

  function openMapping(ids?: string[]) {
    const targetIds = ids && ids.length ? ids : selectedIds;
    if (!targetIds.length && !selectedId) {
      push("Сначала выберите рекламный аккаунт", "info");
      return;
    }
    setMapError("");
    setMappingForm({ client_id: "" });
    setMapOpen(true);
  }

  async function applyMapping() {
    const targetIds = selectedIds.length ? selectedIds : selectedId ? [selectedId] : [];
    if (!targetIds.length) {
      setMapError("Не выбраны рекламные аккаунты.");
      return;
    }
    if (!mappingForm.client_id) {
      setMapError("Выберите клиента.");
      return;
    }
    const targetClient = clients.find((client) => client.id === mappingForm.client_id);
    const targetCurrency = String(targetClient?.default_currency || "USD").toUpperCase();
    const incompatible = targetIds
      .map((id) => accounts.find((account) => account.id === id))
      .filter(
        (account): account is AdAccount =>
          Boolean(account) && String(account.currency || "USD").toUpperCase() !== targetCurrency,
      );
    if (incompatible.length) {
      setMapError(
        `Валюта клиента ${targetCurrency}, а у выбранных аккаунтов: ${[
          ...new Set(incompatible.map((account) => String(account.currency || "USD").toUpperCase())),
        ].join(", ")}. Сначала выберите клиента с той же валютой.`,
      );
      return;
    }
    try {
      setMapLoading(true);
      setMapError("");
      await Promise.all(
        targetIds.map((id) => {
          const account = accounts.find((a) => a.id === id);
          const payload: Record<string, unknown> = {
            client_id: mappingForm.client_id,
            status: "active",
          };
          if (account) payload.metadata = sanitizedMetadataForActivation(account.metadata || {});
          return req<AdAccount>(`/ad-accounts/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
        })
      );
      push(`Аккаунтов привязано: ${targetIds.length}`, "success");
      setMapOpen(false);
      setSelectedIds([]);
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось назначить клиента";
      setMapError(msg);
      push(msg, "error");
    } finally {
      setMapLoading(false);
    }
  }

  async function bulkArchive() {
    const targetIds = selectedIds.length ? selectedIds : selectedId ? [selectedId] : [];
    if (!targetIds.length) {
      push("Сначала выберите рекламный аккаунт", "info");
      return;
    }
    if (!window.confirm(`Архивировать выбранные аккаунты: ${targetIds.length}?`)) return;
    try {
      await Promise.all(targetIds.map((id) => req<{ status: string }>(`/ad-accounts/${id}`, { method: "DELETE" })));
      push(`Аккаунтов архивировано: ${targetIds.length}`, "success");
      setSelectedIds([]);
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось архивировать аккаунты";
      setWarning(msg);
      push(msg, "error");
    }
  }

  async function runSync(accountIds?: string[]) {
    const payload: Record<string, unknown> = {};
    if (accountIds && accountIds.length) payload.account_ids = accountIds;
    await req("/ad-accounts/sync/run", { method: "POST", body: JSON.stringify(payload) });
    await loadData();
  }

  async function syncAll() {
    try {
      await runSync();
      push("Данные обновлены", "success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось обновить данные";
      push(msg, "error");
    }
  }

  async function retrySyncSelected() {
    const targetIds = selectedIds.length ? selectedIds : selectedId ? [selectedId] : [];
    if (!targetIds.length) {
      push("Сначала выберите рекламный аккаунт", "info");
      return;
    }
    try {
      await runSync(targetIds);
      push(`Аккаунтов обновлено: ${targetIds.length}`, "success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось обновить данные";
      push(msg, "error");
    }
  }

  return (
    <>
      <div className="app-shell">
        <AppSidebar active="accounts" />

        <main className="content">
          <header className="topbar role-page-topbar">
            <div className="topbar-left">
              <AppTopTabs active="accounts" sectionLabel="Источники рекламы" />
              <div className="topbar-title">Рекламные аккаунты</div>
              <div className="panel-subtitle">
                Все кабинеты из подключённых платформ и их привязка к клиентам
              </div>
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
                      await loadData();
                      push("Сессия сохранена", "success");
                    }}
                    disabled={!ready}
                  >
                    Сохранить
                  </button>
                </>
              ) : null}
              <button className="primary-btn" onClick={() => void syncAll()}>Обновить данные</button>
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <DataSourcesNav active="accounts" />

          <section className="kpi-grid" style={{ marginTop: 12 }}>
            <article className="kpi-card"><div className="kpi-title">Всего аккаунтов</div><div className="kpi-value">{kpis.total}</div></article>
            <article className="kpi-card good"><div className="kpi-title">Привязаны к клиентам</div><div className="kpi-value">{kpis.mapped}</div></article>
            <article className="kpi-card warn"><div className="kpi-title">Без клиента</div><div className="kpi-value">{kpis.unmapped}</div></article>
            <article className="kpi-card bad"><div className="kpi-title">Ошибки обновления</div><div className="kpi-value">{kpis.errors}</div></article>
          </section>

          <section className="accounts-grid">
            <article className="panel accounts-main">
              <div className="chip-row" style={{ marginTop: 0 }}>
                <button className={`chip-btn ${chip === "all" ? "active" : ""}`} onClick={() => setChip("all")}>Все</button>
                <button className={`chip-btn ${chip === "unmapped" ? "active" : ""}`} onClick={() => setChip("unmapped")}>Без клиента</button>
                <button className={`chip-btn ${chip === "errors" ? "active" : ""}`} onClick={() => setChip("errors")}>С ошибками</button>
                <label>
                  Платформа
                  <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                    {platformOptions.map((p) => (
                      <option key={p} value={p}>{p === "all" ? "Все" : p}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Клиент
                  <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
                    <option value="all">Все</option>
                    {clients.map((c) => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                </label>
                <input className="clientops-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Найти по названию или ID" />
              </div>

              <div className="alert-actions" style={{ marginTop: 10 }}>
                <span className="muted-note" style={{ alignSelf: "center", marginRight: 8 }}>Для выбранных:</span>
                <button className="mini-btn" disabled={!selectedCount} onClick={() => openMapping()}>Назначить клиента</button>
                <button className="mini-btn" disabled={!selectedCount} onClick={() => void bulkArchive()}>Архивировать</button>
                <button className="mini-btn" disabled={!selectedCount} onClick={() => void retrySyncSelected()}>Повторить обновление</button>
              </div>

              <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
                <table className="budgets-table">
                  <thead>
                    <tr>
                      <th><input type="checkbox" checked={rows.length > 0 && rows.every((r) => selectedIds.includes(r.id))} onChange={toggleAllCurrent} /></th>
                      <th>Платформа</th>
                      <th>Аккаунт</th>
                      <th>ID в платформе</th>
                      <th>Последнее обновление</th>
                      <th>Клиент</th>
                      <th>Состояние</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const syncStatus = accountSyncStatus(r);
                      return (
                        <tr key={r.id} className={selectedId === r.id ? "selected" : ""} onClick={() => setSelectedId(r.id)}>
                          <td><input type="checkbox" checked={selectedIds.includes(r.id)} onChange={() => toggleOne(r.id)} onClick={(e) => e.stopPropagation()} /></td>
                          <td>{r.platform}</td>
                          <td>
                            <div className="client-cell">
                              <div className="client-name">{r.name}</div>
                              <div className="client-id">{r.id.slice(0, 8)}</div>
                            </div>
                          </td>
                          <td>{r.external_account_id}</td>
                          <td>{fmtDate(r.last_sync_at || r.updated_at)}</td>
                          <td>{clientNameMap.get(r.client_id) || "--"}</td>
                          <td>
                            <span className={`badge ${syncStatus === "synced" ? "good" : syncStatus === "error" ? "bad" : "warn"}`}>
                              {accountStatusLabel(syncStatus)}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                    {!rows.length ? (
                      <tr>
                        <td colSpan={7} className="muted-note">
                          По выбранным фильтрам аккаунтов нет.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </article>

            <aside className="panel accounts-detail">
              {!selected ? (
                <div className="data-empty-state compact">
                  <strong>Выберите рекламный аккаунт</strong>
                  <span>Здесь появятся его клиент, состояние обновления и доступные действия.</span>
                </div>
              ) : (
                <>
                  <div className="budgets-detail-head">
                    <div>
                      <h3 style={{ margin: 0 }}>Карточка аккаунта</h3>
                      <div className="panel-subtitle">{selected.external_account_id}</div>
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="kpi-title">Привязка к клиенту</div>
                    <div className="budgets-money-line">
                      <strong>{selected.client_id ? "Готово" : "Не назначен"}</strong>
                      <span>{selected.client_id ? "Аккаунт участвует в отчётах клиента" : "Нужно выбрать клиента"}</span>
                    </div>
                    <div className="insight-text">
                      Клиент: {clientNameMap.get(selected.client_id) || "требуется назначение"}
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="kpi-title">Обновление данных</div>
                    <div className="detail-grid">
                      <div className="detail-item"><div className="detail-k">Последняя попытка</div><div className="detail-v">{fmtDate(selected.last_sync_at || selected.updated_at)}</div></div>
                      <div className="detail-item"><div className="detail-k">Состояние</div><div className="detail-v">{accountStatusLabel(accountSyncStatus(selected))}</div></div>
                    </div>
                    {selected.sync_error ? (
                      <div className="alert-card high" style={{ marginTop: 10 }}>
                        <div className="alert-priority high">ОШИБКА ОБНОВЛЕНИЯ</div>
                        <div className="insight-text" style={{ color: "#9e2b2b", marginTop: 8 }}>
                          {selected.sync_error}
                        </div>
                      </div>
                    ) : null}
                    <div style={{ marginTop: 10 }}>
                      <div className="kpi-title">Последние обновления</div>
                      <ul style={{ margin: "8px 0 0", paddingLeft: 16 }}>
                        {syncJobs
                          .filter((j) => j.ad_account_id === selected.id)
                          .slice(0, 3)
                          .map((j) => (
                            <li key={j.id} style={{ marginBottom: 6 }}>
                              {j.status.toUpperCase()} • {fmtDate(j.started_at)}
                            </li>
                          ))}
                        {!syncJobs.some((j) => j.ad_account_id === selected.id) ? <li>Обновлений пока не было</li> : null}
                      </ul>
                    </div>
                  </div>

                  <div className="budgets-detail-actions">
                    <button className="primary-btn" onClick={() => openMapping([selected.id])}>Назначить клиента</button>
                    <button className="ghost-btn" onClick={() => void retrySyncSelected()}>Обновить ещё раз</button>
                  </div>
                </>
              )}
            </aside>
          </section>
        </main>
      </div>

      <div className={`modal-backdrop ${mapOpen ? "" : "hidden-view"}`} onClick={() => !mapLoading && setMapOpen(false)}>
        <div className="modal-card budgets-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-head">
            <div>
              <h3 style={{ margin: 0 }}>Назначить клиента</h3>
              <div className="panel-subtitle">Выбранные аккаунты попадут в отчёты этого клиента.</div>
            </div>
            <button className="ghost-btn" onClick={() => setMapOpen(false)} disabled={mapLoading}>Закрыть</button>
          </div>
          <div className={`warning ${mapError ? "" : "hidden"}`} style={{ marginTop: 10 }}>{mapError}</div>
          <div style={{ marginTop: 10 }}>
            <label>
              Клиент
              <select value={mappingForm.client_id} onChange={(e) => setMappingForm({ client_id: e.target.value })}>
                <option value="">Выберите клиента</option>
                {clients.filter((c) => c.status !== "archived").map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="session-controls" style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <button className="ghost-btn" onClick={() => setMapOpen(false)} disabled={mapLoading}>Отмена</button>
            <button className="primary-btn" onClick={() => void applyMapping()} disabled={mapLoading || !mappingForm.client_id}>
              {mapLoading ? "Сохраняем…" : "Назначить"}
            </button>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
