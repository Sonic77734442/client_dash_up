"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { DataSourcesNav } from "../../components/DataSourcesNav";
import { ToastHost } from "../../components/ToastHost";
import { agencySelectionRequiredMessage, useAgencyContext } from "../../hooks/useAgencyContext";
import { useSession } from "../../hooks/useSession";
import { useScopeRequestGuard } from "../../hooks/useScopeRequestGuard";
import { useToast } from "../../hooks/useToast";
import { ApiRequestError, fetchJson } from "../../lib/api";
import { accountDataFreshness, dataFreshnessMeta, syncRunFeedback } from "../../lib/dataFreshness";
import {
  AdAccount,
  AdAccountSyncJob,
  AdAccountSyncRunResponse,
  AssignmentConflictGroup,
  AssignmentConflictListResponse,
  AssignmentConflictResolveResponse,
  ClientOut,
} from "../../lib/types";

type StatusChip = "all" | "unmapped" | "issues" | "conflicts";

type MappingForm = {
  client_id: string;
};

function fmtDate(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString("ru-RU");
}

function accountSyncStatus(a: AdAccount) {
  return accountDataFreshness(a);
}

function fmtDay(v?: string | null) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleDateString("ru-RU");
}

function fmtMoney(v: number | null | undefined, currency = "USD") {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "--";
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: String(currency || "USD").toUpperCase(),
    maximumFractionDigits: 2,
  }).format(Number(v));
}

const EMPTY_CONFLICTS: AssignmentConflictListResponse = {
  items: [],
  count: 0,
  summary: { conflict_groups: 0, conflicted_accounts: 0, active_budgets: 0 },
};

function accountStatusLabel(status: ReturnType<typeof accountSyncStatus>) {
  return dataFreshnessMeta(status).label;
}

function accountStatusClass(status: ReturnType<typeof accountSyncStatus>) {
  return dataFreshnessMeta(status).tone;
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
  const agencyContext = useAgencyContext({ apiBase: session.apiBase, token: session.token, loadPortfolio: true });
  const soloClient = agencyContext.role === "solo_client";
  const beginScopedRequest = useScopeRequestGuard(
    agencyContext.selectedAgencyId || agencyContext.managedClientId || agencyContext.role || "unknown",
  );
  const activeScopeKey = agencyContext.selectedAgencyId || agencyContext.managedClientId || agencyContext.role || "unknown";
  const activeScopeKeyRef = useRef(activeScopeKey);
  activeScopeKeyRef.current = activeScopeKey;
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [dataLoading, setDataLoading] = useState(true);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [clients, setClients] = useState<ClientOut[]>([]);
  const [syncJobs, setSyncJobs] = useState<AdAccountSyncJob[]>([]);
  const [conflicts, setConflicts] = useState<AssignmentConflictListResponse>(EMPTY_CONFLICTS);
  const [winnerByGroup, setWinnerByGroup] = useState<Record<string, string>>({});
  const [archiveBudgetsByGroup, setArchiveBudgetsByGroup] = useState<Record<string, boolean>>({});
  const [budgetOverrideOfferedByGroup, setBudgetOverrideOfferedByGroup] = useState<Record<string, boolean>>({});
  const [notesByGroup, setNotesByGroup] = useState<Record<string, string>>({});
  const [conflictErrors, setConflictErrors] = useState<Record<string, string>>({});
  const [resolvingGroupId, setResolvingGroupId] = useState("");

  const [chip, setChip] = useState<StatusChip>("all");
  const [platform, setPlatform] = useState("all");
  const [clientId, setClientId] = useState("all");
  const [search, setSearch] = useState("");

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState("");

  const [mapOpen, setMapOpen] = useState(false);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapError, setMapError] = useState("");
  const [mappingTargetIds, setMappingTargetIds] = useState<string[]>([]);
  const [mappingForm, setMappingForm] = useState<MappingForm>({ client_id: "" });

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
  );

  const canManageConflicts = agencyContext.role === "admin" || (
    agencyContext.role === "agency"
    && agencyContext.currentMember?.status === "active"
    && ["owner", "manager"].includes(agencyContext.currentMember.role)
  );

  const loadData = useCallback(async () => {
    const isCurrentRequest = beginScopedRequest();
    if (agencyContext.role === "agency" && !agencyContext.portfolioReady) {
      throw new Error(
        agencyContext.selectionRequired
          ? agencySelectionRequiredMessage()
          : agencyContext.portfolioError || "Не удалось загрузить портфель агентства.",
      );
    }
    if (soloClient && !agencyContext.soloClientReady) {
      throw new Error("Для самостоятельного кабинета должен быть назначен ровно один активный клиент.");
    }
    const conflictPath = agencyContext.role === "agency"
      ? `/ad-accounts/assignment-conflicts?agency_id=${encodeURIComponent(agencyContext.selectedAgencyId)}`
      : "/ad-accounts/assignment-conflicts";
    const [acc, cls, jobs, conflictRows] = await Promise.all([
      req<{ items: AdAccount[] }>("/ad-accounts?status=all"),
      req<{ items: ClientOut[] }>("/clients?status=all"),
      req<{ items: AdAccountSyncJob[] }>("/ad-accounts/sync/jobs?status=all&limit=200"),
      canManageConflicts
        ? req<AssignmentConflictListResponse>(conflictPath)
        : Promise.resolve(EMPTY_CONFLICTS),
    ]);
    if (!isCurrentRequest()) return;
    const allowedClientIds = agencyContext.role === "agency" || soloClient
      ? new Set(agencyContext.clientIds)
      : null;
    const visibleClients = requireItems<ClientOut>(cls, "Клиенты").filter(
      (client) => !allowedClientIds || allowedClientIds.has(client.id),
    );
    const visibleAccounts = requireItems<AdAccount>(acc, "Рекламные аккаунты").filter(
      (account) => !allowedClientIds || allowedClientIds.has(account.client_id),
    );
    const visibleAccountIds = new Set(visibleAccounts.map((account) => account.id));
    setAccounts(visibleAccounts);
    setClients(visibleClients);
    setSyncJobs(
      requireItems<AdAccountSyncJob>(jobs, "История обновлений")
        .filter((job) => !allowedClientIds || visibleAccountIds.has(job.ad_account_id)),
    );
    setConflicts(conflictRows);
    setWarning("");
  }, [
    agencyContext.clientIds,
    agencyContext.portfolioError,
    agencyContext.portfolioReady,
    agencyContext.role,
    agencyContext.selectedAgencyId,
    agencyContext.selectionRequired,
    agencyContext.soloClientReady,
    beginScopedRequest,
    canManageConflicts,
    req,
    soloClient,
  ]);

  useEffect(() => {
    let active = true;
    if (!ready || agencyContext.loading) {
      setDataLoading(true);
      return () => { active = false; };
    }
    setDataLoading(true);
    void loadData()
      .catch((err) => {
        if (active) setWarning(err instanceof Error ? err.message : "Не удалось загрузить рекламные аккаунты");
      })
      .finally(() => {
        if (active) setDataLoading(false);
      });
    return () => { active = false; };
  }, [agencyContext.loading, ready, loadData]);

  useEffect(() => {
    setDataLoading(true);
    setAccounts([]);
    setClients([]);
    setSyncJobs([]);
    setConflicts(EMPTY_CONFLICTS);
    setWinnerByGroup({});
    setArchiveBudgetsByGroup({});
    setBudgetOverrideOfferedByGroup({});
    setNotesByGroup({});
    setConflictErrors({});
    setResolvingGroupId("");
    setSelectedIds([]);
    setSelectedId("");
    setClientId("all");
    setMapOpen(false);
    setMappingTargetIds([]);
  }, [agencyContext.selectedAgencyId]);

  const conflictAccountIds = useMemo(
    () => new Set(conflicts.items.flatMap((group) => group.account_ids)),
    [conflicts.items],
  );

  useEffect(() => {
    const selectableIds = new Set(accounts.filter((account) => !conflictAccountIds.has(account.id)).map((account) => account.id));
    const visibleIds = new Set(accounts.map((account) => account.id));
    setSelectedIds((previous) => previous.filter((id) => selectableIds.has(id)));
    setSelectedId((previous) => (visibleIds.has(previous) ? previous : ""));
  }, [accounts, conflictAccountIds]);

  useEffect(() => {
    if (clientId !== "all" && !clients.some((client) => client.id === clientId)) setClientId("all");
  }, [clientId, clients]);

  const clientNameMap = useMemo(() => new Map(clients.map((c) => [c.id, c.name])), [clients]);
  const accountById = useMemo(() => new Map(accounts.map((account) => [account.id, account])), [accounts]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return accounts
      .filter((a) => (platform === "all" ? true : a.platform === platform))
      .filter((a) => (clientId === "all" ? true : a.client_id === clientId))
      .filter((a) => {
        const s = accountSyncStatus(a);
        if (chip === "all") return true;
        if (chip === "unmapped") return !a.client_id;
        if (chip === "conflicts") return conflictAccountIds.has(a.id);
        return s !== "current";
      })
      .filter((a) => {
        if (!q) return true;
        const hay = `${a.name} ${a.external_account_id} ${a.id} ${clientNameMap.get(a.client_id) || ""}`.toLowerCase();
        return hay.includes(q);
      })
      .sort((a, b) => new Date(b.last_sync_at || b.updated_at || 0).getTime() - new Date(a.last_sync_at || a.updated_at || 0).getTime());
  }, [accounts, platform, clientId, chip, search, clientNameMap, conflictAccountIds]);

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
  const scopedAccountIds = useMemo(() => new Set(accounts.map((account) => account.id)), [accounts]);

  function safeTargetIds(requestedIds: string[]) {
    if (agencyContext.role === "agency" && !agencyContext.portfolioReady) return [];
    return Array.from(new Set(requestedIds)).filter(
      (id) => scopedAccountIds.has(id) && !conflictAccountIds.has(id),
    );
  }

  const kpis = useMemo(() => {
    const total = accounts.length;
    const mapped = accounts.filter((a) => !!a.client_id).length;
    const unmapped = accounts.filter((a) => !a.client_id).length;
    const dataIssues = accounts.filter((a) => accountSyncStatus(a) !== "current").length;
    const conflicted = conflictAccountIds.size;
    return { total, mapped, unmapped, dataIssues, conflicted };
  }, [accounts, conflictAccountIds]);

  const platformOptions = useMemo(
    () => ["all", ...Array.from(new Set(accounts.map((a) => a.platform))).sort()],
    [accounts]
  );

  function toggleOne(id: string) {
    if (conflictAccountIds.has(id)) return;
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function toggleAllCurrent() {
    const ids = rows.filter((row) => !conflictAccountIds.has(row.id)).map((r) => r.id);
    const allSelected = ids.length > 0 && ids.every((id) => selectedIds.includes(id));
    setSelectedIds(allSelected ? selectedIds.filter((id) => !ids.includes(id)) : Array.from(new Set([...selectedIds, ...ids])));
  }

  function openMapping(ids?: string[]) {
    const targetIds = safeTargetIds(ids && ids.length ? ids : selectedIds.length ? selectedIds : [selectedId]);
    if (!targetIds.length) {
      push("Сначала выберите рекламный аккаунт", "info");
      return;
    }
    setMapError("");
    setMappingTargetIds(targetIds);
    setMappingForm({ client_id: "" });
    setMapOpen(true);
  }

  async function applyMapping() {
    const targetIds = safeTargetIds(mappingTargetIds);
    if (!targetIds.length) {
      setMapError("Не выбраны рекламные аккаунты.");
      return;
    }
    if (!mappingForm.client_id) {
      setMapError("Выберите клиента.");
      return;
    }
    if (
      agencyContext.role === "agency"
      && (!agencyContext.portfolioReady || !agencyContext.clientIds.includes(mappingForm.client_id))
    ) {
      setMapError("Выбранный клиент не входит в текущее агентство.");
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
      setMappingTargetIds([]);
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
    const targetIds = safeTargetIds(selectedIds.length ? selectedIds : selectedId ? [selectedId] : []);
    if (!targetIds.length) {
      push("Сначала выберите рекламный аккаунт", "info");
      return;
    }
    if (!window.confirm(`Переместить в архив аккаунты: ${targetIds.length}? Они останутся в истории, но будут исключены из активной работы.`)) return;
    try {
      await Promise.all(targetIds.map((id) => req<{ status: string }>(`/ad-accounts/${id}`, { method: "DELETE" })));
      push(`Перемещено в архив: ${targetIds.length}`, "success");
      setSelectedIds([]);
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось переместить аккаунты в архив";
      setWarning(msg);
      push(msg, "error");
    }
  }

  async function runSync(accountIds?: string[]) {
    const payload: Record<string, unknown> = {};
    const requestedIds = accountIds?.length ? accountIds : accounts.filter((account) => account.status === "active").map((account) => account.id);
    const targetIds = safeTargetIds(requestedIds);
    if (!targetIds.length) {
      throw new Error(
        soloClient
          ? "В вашем кабинете пока нет рекламных аккаунтов для обновления."
          : "Нет рекламных аккаунтов, которые можно безопасно обновить. Сначала разберите конфликты привязки.",
      );
    }
    if (targetIds.length) payload.account_ids = targetIds;
    if (soloClient) payload.client_id = agencyContext.managedClientId;
    const result = await req<AdAccountSyncRunResponse>("/ad-accounts/sync/run", { method: "POST", body: JSON.stringify(payload) });
    await loadData();
    return result;
  }

  async function syncAll() {
    try {
      const result = await runSync(accounts.filter((account) => account.status === "active").map((account) => account.id));
      const feedback = syncRunFeedback(result);
      push(feedback.message, feedback.tone);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось обновить данные";
      push(msg, "error");
    }
  }

  async function retrySyncSelected(explicitIds?: string[]) {
    const targetIds = safeTargetIds(
      explicitIds?.length ? explicitIds : selectedIds.length ? selectedIds : selectedId ? [selectedId] : [],
    );
    if (!targetIds.length) {
      push("Сначала выберите рекламный аккаунт", "info");
      return;
    }
    try {
      const result = await runSync(targetIds);
      const feedback = syncRunFeedback(result);
      push(feedback.message, feedback.tone);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось обновить данные";
      push(msg, "error");
    }
  }

  function selectConflictWinner(groupId: string, accountId: string) {
    setWinnerByGroup((previous) => ({ ...previous, [groupId]: accountId }));
    setArchiveBudgetsByGroup((previous) => ({ ...previous, [groupId]: false }));
    setBudgetOverrideOfferedByGroup((previous) => ({ ...previous, [groupId]: false }));
    setConflictErrors((previous) => ({ ...previous, [groupId]: "" }));
  }

  async function resolveAssignmentConflict(group: AssignmentConflictGroup) {
    const winnerAccountId = winnerByGroup[group.group_id] || "";
    const winner = group.candidates.find((candidate) => candidate.account_id === winnerAccountId);
    if (!winner) {
      setConflictErrors((previous) => ({
        ...previous,
        [group.group_id]: "Выберите аккаунт, который должен остаться активным.",
      }));
      return;
    }

    const loserBudgetCount = group.candidates
      .filter((candidate) => candidate.account_id !== winnerAccountId)
      .reduce((total, candidate) => total + candidate.active_budget_count, 0);
    const archiveBudgets = Boolean(archiveBudgetsByGroup[group.group_id]);
    const confirmation = archiveBudgets
      ? `Оставить аккаунт у клиента «${winner.client_name}» и перенести остальные копии в архив? Вместе с ними будут архивированы активные бюджеты: ${loserBudgetCount}. Действие нельзя отменить из этого экрана.`
      : `Оставить аккаунт у клиента «${winner.client_name}» и перенести остальные копии в архив? Если у них есть активные бюджеты, операция остановится без изменений.`;
    if (!window.confirm(confirmation)) return;

    const requestedScope = activeScopeKeyRef.current;
    const agencyQuery = agencyContext.role === "agency"
      ? `?agency_id=${encodeURIComponent(agencyContext.selectedAgencyId)}`
      : "";
    setResolvingGroupId(group.group_id);
    setConflictErrors((previous) => ({ ...previous, [group.group_id]: "" }));
    try {
      const result = await req<AssignmentConflictResolveResponse>(
        `/ad-accounts/assignment-conflicts/${encodeURIComponent(group.group_id)}/resolve${agencyQuery}`,
        {
          method: "POST",
          body: JSON.stringify({
            winner_account_id: winnerAccountId,
            expected_account_ids: [...group.account_ids],
            group_version: group.group_version,
            loser_budget_policy: archiveBudgets ? "archive" : "reject",
            ...(notesByGroup[group.group_id]?.trim() ? { note: notesByGroup[group.group_id].trim() } : {}),
          }),
        },
      );
      if (activeScopeKeyRef.current !== requestedScope) return;
      setResolvingGroupId("");
      setWinnerByGroup((previous) => ({ ...previous, [group.group_id]: "" }));
      setArchiveBudgetsByGroup((previous) => ({ ...previous, [group.group_id]: false }));
      setBudgetOverrideOfferedByGroup((previous) => ({ ...previous, [group.group_id]: false }));
      setNotesByGroup((previous) => ({ ...previous, [group.group_id]: "" }));
      push(
        result.archived_budget_ids.length
          ? `Привязка исправлена. Архивировано бюджетов: ${result.archived_budget_ids.length}. Запустите обновление данных.`
          : "Привязка исправлена. Запустите обновление данных для выбранного аккаунта.",
        "success",
      );
      await loadData();
    } catch (err) {
      if (activeScopeKeyRef.current !== requestedScope) return;
      setResolvingGroupId("");
      const apiError = err instanceof ApiRequestError ? err : null;
      if (apiError?.code === "assignment_conflict_budgets_present") {
        setBudgetOverrideOfferedByGroup((previous) => ({ ...previous, [group.group_id]: true }));
        setConflictErrors((previous) => ({
          ...previous,
          [group.group_id]: `У копий, которые будут архивированы, есть активные бюджеты (${loserBudgetCount}). Проверьте их и отдельно разрешите архивирование ниже.`,
        }));
        return;
      }
      if (apiError?.code === "assignment_conflict_stale" || apiError?.code === "assignment_conflict_not_found") {
        setConflictErrors((previous) => ({
          ...previous,
          [group.group_id]: "Состав конфликта уже изменился. Список обновлён — выберите владельца ещё раз.",
        }));
        setWinnerByGroup((previous) => ({ ...previous, [group.group_id]: "" }));
        await loadData();
        return;
      }
      const message = err instanceof Error ? err.message : "Не удалось исправить привязку рекламного аккаунта.";
      setConflictErrors((previous) => ({ ...previous, [group.group_id]: message }));
      push(message, "error");
    } finally {
      if (activeScopeKeyRef.current === requestedScope) setResolvingGroupId("");
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
                {soloClient
                  ? "Ваши рекламные кабинеты, их состояние и последние обновления"
                  : "Все кабинеты из подключённых платформ и их привязка к клиентам"}
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
            <article className="kpi-card"><div className="kpi-title">Всего в реестре</div><div className="kpi-value">{dataLoading ? "—" : kpis.total}</div><div className="kpi-meta">Все статусы: активные, неактивные и архивные</div></article>
            <article className="kpi-card good"><div className="kpi-title">{soloClient ? "В вашем кабинете" : "Привязаны к клиентам"}</div><div className="kpi-value">{dataLoading ? "—" : kpis.mapped}</div></article>
            {!soloClient ? <article className="kpi-card warn"><div className="kpi-title">Без клиента</div><div className="kpi-value">{dataLoading ? "—" : kpis.unmapped}</div></article> : null}
            <article className={`kpi-card ${dataLoading ? "" : kpis.dataIssues ? "bad" : "good"}`}><div className="kpi-title">Проблемы данных</div><div className="kpi-value">{dataLoading ? "—" : kpis.dataIssues}</div><div className="kpi-meta">Ошибки, устаревшие или ещё не загруженные данные</div></article>
            {!soloClient ? <article className={`kpi-card ${dataLoading ? "" : kpis.conflicted ? "bad" : "good"}`}>
              <div className="kpi-title">Конфликты привязки</div>
              <div className="kpi-value">{dataLoading ? "—" : kpis.conflicted}</div>
              <div className="kpi-meta">Эти аккаунты исключены из обновления и отчётов до выбора правильного клиента</div>
              {!dataLoading && kpis.conflicted ? (
                <button
                  className="mini-btn"
                  style={{ marginTop: 10 }}
                  onClick={() => document.getElementById("assignment-conflicts")?.scrollIntoView({ behavior: "smooth" })}
                >
                  Разобрать конфликты
                </button>
              ) : null}
            </article> : null}
          </section>

          {!soloClient ? <section className="panel assignment-conflicts-panel" id="assignment-conflicts" style={{ marginTop: 12 }}>
            <div className="assignment-conflicts-head">
              <div>
                <div className="kpi-title">Контроль владельца рекламного аккаунта</div>
                <h2>Конфликты привязки</h2>
                <div className="panel-subtitle">
                  Один кабинет найден у нескольких клиентов. Пока владелец не выбран, его данные не обновляются и не попадают в отчёты.
                </div>
              </div>
              <span className={`badge ${dataLoading ? "" : conflicts.count ? "bad" : "good"}`}>
                {dataLoading ? "Загружаем…" : conflicts.count ? `Нужно разобрать: ${conflicts.count}` : "Всё в порядке"}
              </span>
            </div>

            {dataLoading ? (
              <div className="data-empty-state compact" role="status" style={{ marginTop: 12 }}>
                <strong>Загружаем рекламные аккаунты</strong>
                <span>Проверяем владельцев, последние обновления и возможные конфликты.</span>
              </div>
            ) : null}

            {!dataLoading && !canManageConflicts && agencyContext.role === "agency" ? (
              <div className="data-empty-state compact" style={{ marginTop: 12 }}>
                <strong>Нужны права владельца или менеджера агентства</strong>
                <span>Участник команды может видеть данные, но не может менять владельца рекламного аккаунта.</span>
              </div>
            ) : null}

            {!dataLoading && canManageConflicts && conflicts.count === 0 ? (
              <div className="data-empty-state compact" style={{ marginTop: 12 }}>
                <strong>Конфликтов владения нет</strong>
                <span>Каждый рекламный аккаунт закреплён только за одним клиентом.</span>
              </div>
            ) : null}

            {canManageConflicts ? conflicts.items.map((group) => {
              const selectedWinnerId = winnerByGroup[group.group_id] || "";
              const selectedWinner = group.candidates.find((candidate) => candidate.account_id === selectedWinnerId);
              const loserBudgetCount = selectedWinner
                ? group.candidates
                    .filter((candidate) => candidate.account_id !== selectedWinnerId)
                    .reduce((total, candidate) => total + candidate.active_budget_count, 0)
                : 0;
              const archiveOffered = Boolean(budgetOverrideOfferedByGroup[group.group_id]);
              const isResolving = resolvingGroupId === group.group_id;
              return (
                <article
                  className="assignment-conflict-group"
                  key={group.group_id}
                  data-testid={`assignment-conflict-${group.group_id}`}
                >
                  <div className="assignment-conflict-group-head">
                    <div>
                      <strong>{group.platform} · {group.canonical_external_account_id}</strong>
                      <span>
                        Кандидатов: {group.summary.candidate_count} · клиентов: {group.summary.client_count}
                        {group.summary.latest_stat_date ? ` · данные по ${fmtDay(group.summary.latest_stat_date)}` : ""}
                      </span>
                    </div>
                    {group.summary.active_budget_count ? (
                      <span className="badge warn">Активных бюджетов: {group.summary.active_budget_count}</span>
                    ) : null}
                  </div>

                  <fieldset className="assignment-conflict-candidates" disabled={Boolean(resolvingGroupId)}>
                    <legend>У какого клиента должен остаться этот рекламный аккаунт?</legend>
                    {group.candidates.map((candidate) => {
                      const account = accountById.get(candidate.account_id);
                      const chosen = selectedWinnerId === candidate.account_id;
                      return (
                        <label className={`assignment-conflict-candidate ${chosen ? "selected" : ""}`} key={candidate.account_id}>
                          <input
                            type="radio"
                            name={`winner-${group.group_id}`}
                            value={candidate.account_id}
                            checked={chosen}
                            onChange={() => selectConflictWinner(group.group_id, candidate.account_id)}
                            data-testid={`conflict-winner-${candidate.account_id}`}
                          />
                          <span className="assignment-conflict-candidate-main">
                            <strong>{candidate.client_name}</strong>
                            <span>{candidate.account_name} · {candidate.account_id.slice(0, 8)}</span>
                          </span>
                          <span className="assignment-conflict-candidate-meta">
                            <span>Клиент: {candidate.client_status === "active" ? "активен" : candidate.client_status}</span>
                            <span>Аккаунт: {candidate.account_status === "active" ? "активен" : candidate.account_status}</span>
                            <span>Последняя синхронизация: {fmtDate(account?.last_sync_at)}</span>
                            <span>
                              Последние данные: {fmtDay(candidate.latest_stat?.date)}
                              {candidate.latest_stat ? ` · ${fmtMoney(candidate.latest_stat.spend, candidate.currency)}` : ""}
                            </span>
                            <span>Активных бюджетов: {candidate.active_budget_count}</span>
                          </span>
                        </label>
                      );
                    })}
                  </fieldset>

                  <label className="assignment-conflict-note">
                    Комментарий к решению <span className="muted-note">(необязательно, попадёт в журнал действий)</span>
                    <textarea
                      rows={2}
                      maxLength={1000}
                      value={notesByGroup[group.group_id] || ""}
                      onChange={(event) => setNotesByGroup((previous) => ({
                        ...previous,
                        [group.group_id]: event.target.value,
                      }))}
                      disabled={Boolean(resolvingGroupId)}
                      placeholder="Например: подтверждено менеджером клиента"
                    />
                  </label>

                  {archiveOffered && loserBudgetCount > 0 ? (
                    <label className="assignment-conflict-budget-confirm">
                      <input
                        type="checkbox"
                        checked={Boolean(archiveBudgetsByGroup[group.group_id])}
                        onChange={(event) => setArchiveBudgetsByGroup((previous) => ({
                          ...previous,
                          [group.group_id]: event.target.checked,
                        }))}
                        disabled={Boolean(resolvingGroupId)}
                      />
                      <span>
                        <strong>Разрешаю архивировать активные бюджеты: {loserBudgetCount}</strong>
                        <small>Они останутся в истории, но перестанут участвовать в активном контроле.</small>
                      </span>
                    </label>
                  ) : null}

                  {conflictErrors[group.group_id] ? (
                    <div className="warning assignment-conflict-error" role="alert">{conflictErrors[group.group_id]}</div>
                  ) : null}

                  <div className="assignment-conflict-actions">
                    <span className="muted-note">
                      {selectedWinner
                        ? `Останется: ${selectedWinner.client_name}. Остальные копии будут архивированы.`
                        : "Ничего не изменится, пока вы явно не выберете владельца."}
                    </span>
                    <button
                      className="primary-btn"
                      type="button"
                      disabled={!selectedWinner || Boolean(resolvingGroupId) || (archiveOffered && loserBudgetCount > 0 && !archiveBudgetsByGroup[group.group_id])}
                      onClick={() => void resolveAssignmentConflict(group)}
                      data-testid={`resolve-conflict-${group.group_id}`}
                    >
                      {isResolving ? "Сохраняем…" : "Подтвердить владельца"}
                    </button>
                  </div>
                </article>
              );
            }) : null}
          </section> : null}

          <section className="accounts-grid">
            <article className="panel accounts-main">
              <div className="chip-row" style={{ marginTop: 0 }}>
                <button className={`chip-btn ${chip === "all" ? "active" : ""}`} onClick={() => setChip("all")}>Все</button>
                {!soloClient ? <button className={`chip-btn ${chip === "unmapped" ? "active" : ""}`} onClick={() => setChip("unmapped")}>Без клиента</button> : null}
                <button className={`chip-btn ${chip === "issues" ? "active" : ""}`} onClick={() => setChip("issues")}>С проблемами данных</button>
                {!soloClient ? <button className={`chip-btn ${chip === "conflicts" ? "active" : ""}`} onClick={() => setChip("conflicts")}>
                  Конфликты {conflicts.summary.conflicted_accounts ? `(${conflicts.summary.conflicted_accounts})` : ""}
                </button> : null}
                <label>
                  Платформа
                  <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                    {platformOptions.map((p) => (
                      <option key={p} value={p}>{p === "all" ? "Все" : p}</option>
                    ))}
                  </select>
                </label>
                {!soloClient ? <label>
                  Клиент
                  <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
                    <option value="all">Все</option>
                    {clients.map((c) => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                </label> : null}
                <input className="clientops-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Найти по названию или ID" />
              </div>

              <div className="alert-actions" style={{ marginTop: 10 }}>
                <span className="muted-note" style={{ alignSelf: "center", marginRight: 8 }}>Для выбранных:</span>
                {!soloClient ? <button
                  className="mini-btn"
                  data-testid="bulk-assign-client"
                  disabled={!selectedCount}
                  onClick={() => openMapping()}
                >
                  Назначить клиента
                </button> : null}
                {!soloClient ? <button className="mini-btn" disabled={!selectedCount} onClick={() => void bulkArchive()}>Переместить в архив</button> : null}
                <button className="mini-btn" disabled={!selectedCount} onClick={() => void retrySyncSelected()}>Повторить обновление</button>
              </div>

              <div className="budgets-table-wrap" style={{ marginTop: 10 }}>
                <table className="budgets-table">
                  <thead>
                    <tr>
                      <th>
                        <input
                          type="checkbox"
                          aria-label="Выбрать все доступные аккаунты"
                          checked={rows.some((row) => !conflictAccountIds.has(row.id)) && rows.filter((row) => !conflictAccountIds.has(row.id)).every((row) => selectedIds.includes(row.id))}
                          onChange={toggleAllCurrent}
                        />
                      </th>
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
                      const hasAssignmentConflict = conflictAccountIds.has(r.id);
                      return (
                        <tr key={r.id} className={selectedId === r.id ? "selected" : ""} onClick={() => setSelectedId(r.id)}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={hasAssignmentConflict ? "Сначала разберите конфликт привязки" : `Выбрать ${r.name}`}
                              checked={selectedIds.includes(r.id)}
                              disabled={hasAssignmentConflict}
                              onChange={() => toggleOne(r.id)}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </td>
                          <td>{r.platform}</td>
                          <td>
                            <div className="client-cell">
                              <div className="client-name">{r.name}</div>
                              <div className="client-id">{r.id.slice(0, 8)}</div>
                            </div>
                          </td>
                          <td>{r.external_account_id}</td>
                          <td>{fmtDate(r.last_sync_at)}</td>
                          <td>{clientNameMap.get(r.client_id) || "--"}</td>
                          <td>
                            {hasAssignmentConflict ? (
                              <span className="badge bad">Конфликт привязки</span>
                            ) : (
                              <span className={`badge ${accountStatusClass(syncStatus)}`}>
                                {accountStatusLabel(syncStatus)}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                    {!rows.length ? (
                      <tr>
                        <td colSpan={7} className="muted-note">
                          {dataLoading ? "Загружаем рекламные аккаунты…" : "По выбранным фильтрам аккаунтов нет."}
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
                      <div className="kpi-title">{soloClient ? "Клиентский кабинет" : "Привязка к клиенту"}</div>
                    <div className="budgets-money-line">
                      <strong>
                        {conflictAccountIds.has(selected.id)
                          ? "Нужно подтвердить владельца"
                          : selected.client_id ? "Привязка настроена" : "Не назначен"}
                      </strong>
                      <span>
                        {conflictAccountIds.has(selected.id)
                          ? "Аккаунт временно исключён из обновления и отчётов"
                          : selected.client_id ? "Аккаунт участвует в отчётах клиента" : "Нужно выбрать клиента"}
                      </span>
                    </div>
                    <div className="insight-text">
                      Клиент: {clientNameMap.get(selected.client_id) || "требуется назначение"}
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="kpi-title">Обновление данных</div>
                    <div className="detail-grid">
                      <div className="detail-item"><div className="detail-k">Последняя успешная синхронизация</div><div className="detail-v">{fmtDate(selected.last_sync_at)}</div></div>
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
                              {j.status === "success" ? "Успешно" : "Ошибка"} • {fmtDate(j.started_at)}
                            </li>
                          ))}
                        {!syncJobs.some((j) => j.ad_account_id === selected.id) ? <li>Обновлений пока не было</li> : null}
                      </ul>
                    </div>
                  </div>

                    {conflictAccountIds.has(selected.id) ? (
                    <div className="alert-card high">
                      <div className="alert-priority high">КОНФЛИКТ ПРИВЯЗКИ</div>
                      <div className="insight-text" style={{ marginTop: 8 }}>
                        Обычные действия отключены, чтобы не изменить не тот экземпляр аккаунта.
                      </div>
                      <button
                        className="primary-btn"
                        style={{ marginTop: 10 }}
                        onClick={() => document.getElementById("assignment-conflicts")?.scrollIntoView({ behavior: "smooth" })}
                      >
                        Выбрать правильного владельца
                      </button>
                    </div>
                  ) : (
                      <div className="budgets-detail-actions">
                        {!soloClient ? <button className="primary-btn" onClick={() => openMapping([selected.id])}>Назначить клиента</button> : null}
                        <button className="ghost-btn" onClick={() => void retrySyncSelected([selected.id])}>Обновить ещё раз</button>
                    </div>
                  )}
                </>
              )}
            </aside>
          </section>
        </main>
      </div>

      {!soloClient ? <div className={`modal-backdrop ${mapOpen ? "" : "hidden-view"}`} onClick={() => !mapLoading && setMapOpen(false)}>
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
      </div> : null}

      <ToastHost toasts={toasts} />
    </>
  );
}
