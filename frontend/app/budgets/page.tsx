"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { ProviderBudgetControl } from "../../components/ProviderBudgetControl";
import { ToastHost } from "../../components/ToastHost";
import { agencySelectionRequiredMessage, useAgencyContext } from "../../hooks/useAgencyContext";
import { useSession } from "../../hooks/useSession";
import { useScopeRequestGuard } from "../../hooks/useScopeRequestGuard";
import { useToast } from "../../hooks/useToast";
import { fetchJson, getQuery } from "../../lib/api";
import { AdAccount, AdStat, Budget, Client } from "../../lib/types";

type StatusFilter = "active" | "archived" | "all";
type RangePreset = "qtd" | "30" | "90";

type BudgetRow = Budget & {
  resolvedClientName: string;
  resolvedAccountName: string | null;
  spend: number;
  usagePercent: number | null;
  pace: "on_track" | "overspending" | "underspending" | "unknown";
};

type BudgetForm = {
  scope: "client" | "account";
  client_id: string;
  account_id: string;
  amount: string;
  currency: string;
  period_type: "monthly" | "custom";
  start_date: string;
  end_date: string;
  note: string;
};

type CreateCapHint = {
  loading: boolean;
  level: "info" | "ok" | "warn";
  text: string;
};

type BudgetTransferResponse = {
  source_budget: Budget;
  target_budget: Budget;
  transferred_amount: string;
};

type BudgetTransferOut = {
  id: number;
  source_budget_id: string;
  target_budget_id: string;
  amount: string;
  note: string | null;
  changed_by: string | null;
  created_at: string;
};

function normalizeCurrency(value: string | null | undefined) {
  const currency = String(value || "USD").trim().toUpperCase();
  try {
    new Intl.NumberFormat("en-US", { style: "currency", currency }).format(0);
    return currency;
  } catch {
    return "USD";
  }
}

function isSupportedCurrency(value: string) {
  const currency = value.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(currency)) return false;
  try {
    new Intl.NumberFormat("en-US", { style: "currency", currency }).format(0);
    return true;
  } catch {
    return false;
  }
}

function fmtMoney(v: number | null | undefined, currency = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: normalizeCurrency(currency),
    maximumFractionDigits: 0,
  }).format(v || 0);
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function rangeFromPreset(preset: RangePreset) {
  const to = new Date();
  const from = new Date(to);
  if (preset === "qtd") {
    const quarterStartMonth = Math.floor(to.getMonth() / 3) * 3;
    from.setMonth(quarterStartMonth, 1);
  } else {
    from.setDate(from.getDate() - (Number(preset) - 1));
  }
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { date_from: fmt(from), date_to: fmt(to) };
}

function statusClass(pace: BudgetRow["pace"]) {
  if (pace === "overspending") return "bad";
  if (pace === "underspending") return "warn";
  if (pace === "on_track") return "good";
  return "";
}

function paceLabel(pace: BudgetRow["pace"]) {
  if (pace === "overspending") return "ПЕРЕРАСХОД";
  if (pace === "underspending") return "НИЖЕ ПЛАНА";
  if (pace === "on_track") return "ПО ПЛАНУ";
  return "НЕТ ДАННЫХ";
}

function budgetStatusLabel(status?: string | null) {
  return status === "archived" ? "В архиве" : status === "active" || !status ? "Активен" : status;
}

function budgetScopeLabel(scope: Budget["scope"]) {
  return scope === "account" ? "Аккаунт" : "Клиент";
}

function buildCsv(rows: BudgetRow[]) {
  const head = ["scope", "client", "account", "budget", "currency", "usage_percent", "pace", "period_start", "period_end", "status"];
  const lines = rows.map((r) => [
    r.scope,
    r.resolvedClientName,
    r.resolvedAccountName || "",
    r.amount,
    r.currency || "USD",
    r.usagePercent == null ? "" : r.usagePercent.toFixed(1),
    r.pace,
    r.start_date || "",
    r.end_date || "",
    r.status || "active",
  ]);
  return [head, ...lines]
    .map((line) => line.map((x) => `"${String(x).replaceAll("\"", "\"\"")}"`).join(","))
    .join("\n");
}

function defaultCreateForm(): BudgetForm {
  return {
    scope: "client",
    client_id: "",
    account_id: "",
    amount: "",
    currency: "USD",
    period_type: "monthly",
    start_date: todayIso().slice(0, 8) + "01",
    end_date: todayIso(),
    note: "",
  };
}

export default function BudgetsPage() {
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const agencyContext = useAgencyContext({ apiBase: session.apiBase, token: session.token, loadPortfolio: true });
  const agencyScopeKey = agencyContext.selectedAgencyId || agencyContext.role || "unknown";
  const beginScopedRequest = useScopeRequestGuard(agencyScopeKey);
  const beginTransferRequest = useScopeRequestGuard(agencyScopeKey);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [clients, setClients] = useState<Client[]>([]);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [stats, setStats] = useState<AdStat[]>([]);

  const [preset, setPreset] = useState<RangePreset>("qtd");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [clientId, setClientId] = useState("");
  const [search, setSearch] = useState("");

  const [page, setPage] = useState(1);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [selectedBudgetId, setSelectedBudgetId] = useState("");

  const [createOpen, setCreateOpen] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createCapHint, setCreateCapHint] = useState<CreateCapHint>({ loading: false, level: "info", text: "" });
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferLoading, setTransferLoading] = useState(false);
  const [transferError, setTransferError] = useState("");
  const [transferTargetAccountId, setTransferTargetAccountId] = useState("");
  const [transferAmount, setTransferAmount] = useState("");
  const [transferHistory, setTransferHistory] = useState<BudgetTransferOut[]>([]);
  const [transferHistoryLoading, setTransferHistoryLoading] = useState(false);
  const [auditFilter, setAuditFilter] = useState<"all" | "transfers" | "notes">("all");
  const [transferDirection, setTransferDirection] = useState<"all" | "incoming" | "outgoing">("all");
  const [actionLoading, setActionLoading] = useState(false);
  const [actionStatus, setActionStatus] = useState("");
  const [createForm, setCreateForm] = useState<BudgetForm>(defaultCreateForm());

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(session.apiBase, path, session.token, init),
    [session.apiBase, session.token]
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
    if (agencyContext.role === "solo_client" && !agencyContext.soloClientReady) {
      throw new Error("Для самостоятельного кабинета должен быть назначен ровно один активный клиент.");
    }
    const range = rangeFromPreset(preset);
    const scopedClientId = agencyContext.role === "solo_client" ? agencyContext.managedClientId : clientId;
    const budgetQuery = getQuery({
      status,
      client_id: scopedClientId || undefined,
      date_from: range.date_from,
      date_to: range.date_to,
    });
    const statsQuery = getQuery({
      date_from: range.date_from,
      date_to: range.date_to,
      client_id: scopedClientId || undefined,
    });
    const [c, a, b, statPayload] = await Promise.all([
      req<{ items: Client[] }>("/clients?status=active"),
      req<{ items: AdAccount[] }>("/ad-accounts?status=active"),
      req<{ items: Budget[] }>(`/budgets${budgetQuery}`),
      req<{ items: AdStat[] }>(`/ad-stats${statsQuery}`),
    ]);
    if (!isCurrentRequest()) return;
    const allowedClientIds = agencyContext.role === "agency" || agencyContext.role === "solo_client"
      ? new Set(agencyContext.clientIds)
      : null;
    setClients((c.items || []).filter((client) => !allowedClientIds || allowedClientIds.has(client.id)));
    setAccounts((a.items || []).filter((account) => !allowedClientIds || allowedClientIds.has(account.client_id)));
    setBudgets((b.items || []).filter((budget) => !allowedClientIds || allowedClientIds.has(budget.client_id)));
    const visibleAccountIds = new Set(
      (a.items || [])
        .filter((account) => !allowedClientIds || allowedClientIds.has(account.client_id))
        .map((account) => account.id),
    );
    setStats((statPayload.items || []).filter((stat) => visibleAccountIds.has(String(stat.ad_account_id || ""))));
    setWarning("");
  }, [
    agencyContext.clientIds,
    agencyContext.portfolioError,
    agencyContext.portfolioReady,
    agencyContext.role,
    agencyContext.managedClientId,
    agencyContext.soloClientReady,
    agencyContext.selectionRequired,
    beginScopedRequest,
    req,
    preset,
    status,
    clientId,
  ]);

  useEffect(() => {
    if (!ready || agencyContext.loading) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Не удалось загрузить бюджеты"));
  }, [agencyContext.loading, ready, loadData]);

  useEffect(() => {
    setClientId("");
    setClients([]);
    setAccounts([]);
    setBudgets([]);
    setStats([]);
    setSelectedBudgetId("");
    setCreateOpen(false);
    setTransferOpen(false);
    setTransferHistory([]);
  }, [agencyContext.selectedAgencyId]);

  useEffect(() => {
    if (clientId && !clients.some((client) => client.id === clientId)) setClientId("");
  }, [clientId, clients]);

  useEffect(() => {
    if (agencyContext.role === "solo_client" && clients.length === 1 && clientId !== clients[0].id) {
      setClientId(clients[0].id);
    }
  }, [agencyContext.role, clientId, clients]);

  const clientMap = useMemo(() => new Map(clients.map((c) => [c.id, c.name])), [clients]);
  const accountMap = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const selectedRange = rangeFromPreset(preset);
    const mapped = (budgets || []).map((b) => {
      const accountName = b.account_id ? accountMap.get(b.account_id)?.name || b.account_id : null;
      const effectiveFrom = b.start_date && b.start_date > selectedRange.date_from ? b.start_date : selectedRange.date_from;
      const effectiveTo = b.end_date && b.end_date < selectedRange.date_to ? b.end_date : selectedRange.date_to;
      const spend = stats.reduce((sum, stat) => {
        const statAccountId = String(stat.ad_account_id || "");
        const account = accountMap.get(statAccountId);
        if (!account || account.client_id !== b.client_id) return sum;
        if (b.scope === "account" && statAccountId !== String(b.account_id || "")) return sum;
        if (stat.date < effectiveFrom || stat.date > effectiveTo) return sum;
        return sum + Number(stat.spend || 0);
      }, 0);
      const budget = Number(b.amount || 0);
      const usagePercent = budget > 0 ? (spend / budget) * 100 : null;
      const pace: BudgetRow["pace"] =
        usagePercent == null ? "unknown" : usagePercent >= 100 ? "overspending" : usagePercent < 45 ? "underspending" : "on_track";
      return {
        ...b,
        resolvedClientName: clientMap.get(b.client_id) || b.client_id,
        resolvedAccountName: accountName,
        spend,
        usagePercent,
        pace,
      };
    });

    const filtered = mapped.filter((r) => {
      if (!q) return true;
      const hay = `${r.resolvedClientName} ${r.resolvedAccountName || ""} ${r.client_id} ${r.account_id || ""}`.toLowerCase();
      return hay.includes(q);
    });

    return filtered.sort((a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime());
  }, [budgets, search, clientMap, accountMap, stats, preset]);

  useEffect(() => {
    if (!rows.length) {
      setSelectedBudgetId("");
      return;
    }
    if (!selectedBudgetId || !rows.some((r) => r.id === selectedBudgetId)) {
      setSelectedBudgetId(rows[0].id || "");
    }
  }, [rows, selectedBudgetId]);

  const selected = useMemo(() => rows.find((x) => x.id === selectedBudgetId) || null, [rows, selectedBudgetId]);
  const selectedIsScoped = !!selected
    && (!["agency", "solo_client"].includes(agencyContext.role || "")
      || (agencyContext.portfolioReady && agencyContext.clientIds.includes(selected.client_id)));

  const loadTransferHistory = useCallback(async (budgetId: string, direction: "all" | "incoming" | "outgoing") => {
    const isCurrentRequest = beginTransferRequest();
    setTransferHistoryLoading(true);
    try {
      const rows = await req<BudgetTransferOut[]>(`/budgets/${budgetId}/transfers${getQuery({ direction, limit: 20 })}`);
      if (isCurrentRequest()) setTransferHistory(Array.isArray(rows) ? rows : []);
    } catch {
      if (isCurrentRequest()) setTransferHistory([]);
    } finally {
      if (isCurrentRequest()) setTransferHistoryLoading(false);
    }
  }, [beginTransferRequest, req]);

  useEffect(() => {
    if (!selected?.id || !selectedIsScoped) {
      setTransferHistory([]);
      setTransferHistoryLoading(false);
      return;
    }
    void loadTransferHistory(selected.id, transferDirection);
  }, [selected?.id, selectedIsScoped, transferDirection, loadTransferHistory]);

  const pages = Math.max(1, Math.ceil(rows.length / rowsPerPage));
  const safePage = Math.max(1, Math.min(page, pages));
  const pageRows = useMemo(() => {
    const start = (safePage - 1) * rowsPerPage;
    return rows.slice(start, start + rowsPerPage);
  }, [rows, safePage, rowsPerPage]);

  useEffect(() => {
    setPage((p) => Math.max(1, Math.min(p, pages)));
  }, [pages]);

  const kpis = useMemo(() => {
    const active = rows.filter((r) => (r.status || "active") === "active");
    const effectiveBudgets: BudgetRow[] = [];
    const rowsByClient = new Map<string, BudgetRow[]>();
    for (const row of active) {
      const group = rowsByClient.get(row.client_id) || [];
      group.push(row);
      rowsByClient.set(row.client_id, group);
    }
    for (const clientRows of rowsByClient.values()) {
      const clientCaps = clientRows.filter((row) => row.scope === "client");
      effectiveBudgets.push(...(clientCaps.length ? clientCaps : clientRows.filter((row) => row.scope === "account")));
    }

    const currencies = new Set(effectiveBudgets.map((r) => String(r.currency || "USD").toUpperCase()));
    const totalBudget = effectiveBudgets.reduce((acc, x) => acc + Number(x.amount || 0), 0);
    const totalSpend = stats.reduce((sum, stat) => {
      const accountId = String(stat.ad_account_id || "");
      const account = accountMap.get(accountId);
      if (!account) return sum;
      const coveredByBudget = effectiveBudgets.some((budget) => {
        if (budget.client_id !== account.client_id) return false;
        if (budget.scope === "account" && String(budget.account_id || "") !== accountId) return false;
        if (budget.start_date && stat.date < budget.start_date) return false;
        if (budget.end_date && stat.date > budget.end_date) return false;
        return true;
      });
      return coveredByBudget ? sum + Number(stat.spend || 0) : sum;
    }, 0);
    const atRisk = effectiveBudgets.filter((x) => x.pace === "overspending").length;
    return {
      activeBudgets: active.length,
      totalBudget,
      totalSpend,
      atRisk,
      currency: currencies.size === 1 ? [...currencies][0] : currencies.size === 0 ? "USD" : null,
      hasMixedCurrencies: currencies.size > 1,
    };
  }, [rows, stats, accountMap]);

  const totalBudgetLabel = kpis.currency ? fmtMoney(kpis.totalBudget, kpis.currency) : "Разные валюты";
  const totalSpendLabel = kpis.currency ? fmtMoney(kpis.totalSpend, kpis.currency) : "Разные валюты";
  const efficiencyLabel =
    kpis.currency && kpis.totalBudget > 0
      ? `${((kpis.totalSpend / kpis.totalBudget) * 100).toFixed(1)}%`
      : "--";

  const parsedCreateAmount = Number(createForm.amount);
  const isAmountValid = Number.isFinite(parsedCreateAmount) && parsedCreateAmount > 0;
  const isCurrencyValid = isSupportedCurrency(createForm.currency);
  const isDateRangeValid = !!createForm.start_date && !!createForm.end_date && createForm.start_date <= createForm.end_date;
  const isScopeValid = createForm.scope === "client" || !!createForm.account_id;
  const canCreate = !!createForm.client_id && isAmountValid && isCurrencyValid && isDateRangeValid && isScopeValid;
  const createCapBlocksSubmit = createCapHint.level === "warn" && !!createCapHint.text;

  function openCreateModal() {
    setCreateError("");
    setCreateCapHint({ loading: false, level: "info", text: "" });
    const next = defaultCreateForm();
    if (agencyContext.role === "solo_client" && clients.length === 1) {
      next.client_id = clients[0].id;
      next.currency = clients[0].default_currency || "USD";
    }
    setCreateForm(next);
    setCreateOpen(true);
  }

  async function createBudget() {
    if (
      (agencyContext.role === "agency" || agencyContext.role === "solo_client")
      && (
        !agencyContext.portfolioReady
        || !agencyContext.clientIds.includes(createForm.client_id)
        || (
          createForm.scope === "account"
          && !accounts.some((account) => account.id === createForm.account_id && account.client_id === createForm.client_id)
        )
      )
    ) {
      setCreateError(
        agencyContext.role === "solo_client"
          ? "Клиент или рекламный аккаунт не входит в ваш кабинет."
          : "Клиент или аккаунт не входит в выбранное агентство.",
      );
      return;
    }
    if (!isAmountValid) {
      setCreateError("Сумма должна быть больше нуля.");
      return;
    }
    if (!isCurrencyValid) {
      setCreateError("Укажите корректный трёхбуквенный код валюты, например USD.");
      return;
    }
    if (!isDateRangeValid) {
      setCreateError("Дата окончания не может быть раньше даты начала.");
      return;
    }
    if (!canCreate) {
      setCreateError("Заполните все обязательные поля.");
      return;
    }
    if (createCapBlocksSubmit) {
      setCreateError("Устраните конфликт с лимитом бюджета.");
      return;
    }
    try {
      setCreateLoading(true);
      setCreateError("");
      setWarning("");
      await req<Budget>("/budgets", {
        method: "POST",
        body: JSON.stringify({
          scope: createForm.scope,
          client_id: createForm.client_id,
          account_id: createForm.scope === "account" ? createForm.account_id : null,
          amount: createForm.amount,
          currency: createForm.currency,
          period_type: createForm.period_type,
          start_date: createForm.start_date,
          end_date: createForm.end_date,
          note: createForm.note || null,
        }),
      });
      push("Бюджет создан", "success");
      setCreateOpen(false);
      setCreateForm(defaultCreateForm());
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось создать бюджет";
      setWarning(msg);
      setCreateError(msg);
      push(msg, "error");
    } finally {
      setCreateLoading(false);
    }
  }

  async function adjustSelected(deltaFactor: number) {
    if (!selected?.id || !selectedIsScoped) {
      const msg = "У выбранного бюджета нет идентификатора. Обновите список и повторите попытку.";
      setWarning(msg);
      push(msg, "error");
      return;
    }
    try {
      setActionLoading(true);
      setActionStatus("");
      const oldAmount = Number(selected.amount || 0);
      const nextAmount = Math.max(0, oldAmount * deltaFactor);
      await req<Budget>(`/budgets/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({ amount: nextAmount.toFixed(2) }),
      });
      const msg = `Бюджет изменён до ${fmtMoney(nextAmount, selected.currency || "USD")}`;
      setActionStatus(msg);
      push(msg, "success");
      await loadData();
      await loadTransferHistory(selected.id, transferDirection);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось изменить бюджет";
      setWarning(msg);
      setActionStatus(`Не удалось изменить бюджет: ${msg}`);
      push(msg, "error");
    } finally {
      setActionLoading(false);
    }
  }

  async function archiveSelected() {
    if (!selected?.id || !selectedIsScoped) {
      const msg = "У выбранного бюджета нет идентификатора. Обновите список и повторите попытку.";
      setWarning(msg);
      push(msg, "error");
      return;
    }
    try {
      setActionLoading(true);
      setActionStatus("");
      await req<Budget>(`/budgets/${selected.id}`, { method: "DELETE" });
      const msg = "Бюджет перемещён в архив. Чтобы вернуть его, выберите статус «Архив» или «Все» и нажмите «Восстановить».";
      setActionStatus(msg);
      push("Бюджет перемещён в архив", "success");
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось архивировать бюджет";
      setWarning(msg);
      setActionStatus(`Не удалось архивировать бюджет: ${msg}`);
      push(msg, "error");
    } finally {
      setActionLoading(false);
    }
  }

  async function restoreSelected() {
    if (!selected?.id || !selectedIsScoped) {
      const msg = "У выбранного бюджета нет идентификатора. Обновите список и повторите попытку.";
      setWarning(msg);
      push(msg, "error");
      return;
    }
    try {
      setActionLoading(true);
      setActionStatus("");
      await req<Budget>(`/budgets/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "active" }),
      });
      const msg = "Бюджет восстановлен и снова активен.";
      setActionStatus(msg);
      push(msg, "success");
      await loadData();
      await loadTransferHistory(selected.id, transferDirection);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось восстановить бюджет";
      setWarning(msg);
      setActionStatus(`Не удалось восстановить бюджет: ${msg}`);
      push(msg, "error");
    } finally {
      setActionLoading(false);
    }
  }

  function openTransferModal() {
    if (!selected || !selectedIsScoped || selected.scope !== "account" || !selected.account_id || selected.status !== "active") return;
    const preferredAmount = Math.max(0, Math.min(100, Number(selected.amount || 0)));
    setTransferError("");
    setTransferTargetAccountId("");
    setTransferAmount(preferredAmount > 0 ? preferredAmount.toFixed(2) : "");
    setTransferOpen(true);
  }

  async function submitTransfer() {
    if (!selected?.id || !selectedIsScoped || selected.scope !== "account" || !selected.account_id) {
      setTransferError("Сначала выберите активный бюджет рекламного аккаунта.");
      return;
    }
    const amount = Number(transferAmount);
    if (!transferTargetAccountId) {
      setTransferError("Выберите аккаунт-получатель.");
      return;
    }
    if (!accounts.some(
      (account) => account.id === transferTargetAccountId && account.client_id === selected.client_id,
    )) {
      setTransferError("Целевой аккаунт не входит в ваш клиентский кабинет.");
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setTransferError("Сумма перевода должна быть больше нуля.");
      return;
    }
    if (amount > Number(selected.amount || 0)) {
      setTransferError("Сумма перевода превышает исходный бюджет.");
      return;
    }
    try {
      setTransferLoading(true);
      setTransferError("");
      const res = await req<BudgetTransferResponse>(`/budgets/${selected.id}/transfer`, {
        method: "POST",
        body: JSON.stringify({
          target_account_id: transferTargetAccountId,
          amount: amount.toFixed(2),
          note: `Transfer from ${selected.account_id} to ${transferTargetAccountId}`,
        }),
      });
      const msg = `Переведено ${fmtMoney(Number(res.transferred_amount || amount), selected.currency || "USD")}`;
      setActionStatus(msg);
      push(msg, "success");
      setTransferOpen(false);
      await loadData();
      await loadTransferHistory(selected.id, transferDirection);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось перевести бюджет";
      setTransferError(msg);
      push(msg, "error");
    } finally {
      setTransferLoading(false);
    }
  }

  function exportCsv() {
    const csv = buildCsv(rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `budgets-ledger-${todayIso()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    push("CSV-файл выгружен", "info");
  }

  const accountsForClient = useMemo(
    () => accounts.filter((a) => a.client_id === createForm.client_id),
    [accounts, createForm.client_id]
  );
  const transferAccountOptions = useMemo(() => {
    if (!selected?.client_id || !selected.account_id) return [] as AdAccount[];
    return accounts.filter((a) => a.client_id === selected.client_id && a.id !== selected.account_id && a.status === "active");
  }, [accounts, selected]);
  const transferPreview = useMemo(() => {
    if (!selected || selected.scope !== "account" || !selected.account_id) return null;
    const amount = Number(transferAmount || 0);
    const sourceBefore = Number(selected.amount || 0);
    const sourceAfter = sourceBefore - amount;
    const targetBudget = rows.find(
      (r) =>
        r.scope === "account" &&
        r.client_id === selected.client_id &&
        r.account_id === transferTargetAccountId &&
        (r.status || "active") === "active"
    );
    const targetBefore = Number(targetBudget?.amount || 0);
    const targetAfter = targetBefore + amount;
    return {
      amount,
      sourceBefore,
      sourceAfter,
      targetBefore,
      targetAfter,
      validAmount: Number.isFinite(amount) && amount > 0,
      validSource: sourceAfter >= 0,
      hasTarget: !!transferTargetAccountId,
    };
  }, [selected, transferAmount, rows, transferTargetAccountId]);
  const canSubmitTransfer = !!transferPreview?.hasTarget && !!transferPreview?.validAmount && !!transferPreview?.validSource && !transferLoading;

  useEffect(() => {
    let cancelled = false;
    async function runCapHint() {
      if (!createOpen || !createForm.client_id || !createForm.start_date || !createForm.end_date || !isAmountValid || !isDateRangeValid) {
        setCreateCapHint({ loading: false, level: "info", text: "" });
        return;
      }

      setCreateCapHint({ loading: true, level: "info", text: "Проверяем лимит бюджета…" });
      try {
        const q = getQuery({
          client_id: createForm.client_id,
          status: "active",
          date_from: createForm.start_date,
          date_to: createForm.end_date,
        });
        const res = await req<{ items: Budget[] }>(`/budgets${q}`);
        if (cancelled) return;

        const rows = (res.items || []).filter((b) => (b.status || "active") === "active");
        const clientBudget = rows.find((b) => b.scope === "client");
        const accountSum = rows
          .filter((b) => b.scope === "account")
          .reduce((acc, b) => acc + Number(b.amount || 0), 0);
        const amount = Number(createForm.amount || 0);

        if (createForm.scope === "account") {
          if (!clientBudget) {
            setCreateCapHint({
              loading: false,
              level: "info",
              text: "На этот период нет активного бюджета клиента. Ограничение всё равно будет проверено при сохранении.",
            });
            return;
          }
          const clientCap = Number(clientBudget.amount || 0);
          const projected = accountSum + amount;
          if (projected > clientCap) {
            setCreateCapHint({
              loading: false,
              level: "warn",
              text: `План по аккаунтам ${fmtMoney(projected)} превышает лимит клиента ${fmtMoney(clientCap)}.`,
            });
          } else {
            setCreateCapHint({
              loading: false,
              level: "ok",
              text: `План по аккаунтам: ${fmtMoney(projected)} из лимита клиента ${fmtMoney(clientCap)}.`,
            });
          }
          return;
        }

        if (amount < accountSum) {
          setCreateCapHint({
            loading: false,
            level: "warn",
            text: `Бюджет клиента ${fmtMoney(amount)} меньше суммы бюджетов аккаунтов ${fmtMoney(accountSum)}.`,
          });
        } else {
          setCreateCapHint({
            loading: false,
            level: "ok",
            text: `Бюджет клиента ${fmtMoney(amount)} покрывает бюджеты аккаунтов ${fmtMoney(accountSum)}.`,
          });
        }
      } catch {
        if (!cancelled) {
          setCreateCapHint({
            loading: false,
            level: "info",
            text: "Предварительная проверка лимита недоступна. Проверим его при сохранении.",
          });
        }
      }
    }
    void runCapHint();
    return () => {
      cancelled = true;
    };
  }, [
    createOpen,
    createForm.client_id,
    createForm.scope,
    createForm.start_date,
    createForm.end_date,
    createForm.amount,
    isAmountValid,
    isDateRangeValid,
    req,
  ]);

  return (
    <>
      <div className="app-shell budgets-shell">
        <AppSidebar active="budgets" subtitle="Финансовый контроль" className="sidebar budgets-sidebar" />

        <main className="content budgets-content">
          <header className="topbar budgets-topbar">
            <div className="topbar-left">
              <AppTopTabs active="budgets" />
              <div className="topbar-title">Плановые бюджеты</div>
              <div className="panel-subtitle">
                {agencyContext.role === "solo_client"
                  ? "Внутренние лимиты для отчётности — они не меняют настройки рекламных платформ."
                  : "Внутренние лимиты клиентов для отчётности — они не меняют настройки рекламных платформ."}
              </div>
            </div>
            <div className="session-controls">
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
                        await loadData();
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
              <button className="primary-btn" onClick={openCreateModal}>Создать плановый бюджет</button>
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="kpi-grid budgets-kpis">
            <article className="kpi-card">
              <div className="kpi-title">Активные бюджеты</div>
              <div className="kpi-value">{kpis.activeBudgets}</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-title">Общий бюджет</div>
              <div className="kpi-value">{totalBudgetLabel}</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-title">Общие расходы</div>
              <div className="kpi-value">{totalSpendLabel}</div>
            </article>
            <article className="kpi-card bad">
              <div className="kpi-title">Требуют внимания</div>
              <div className="kpi-value">{kpis.atRisk}</div>
            </article>
          </section>
          {kpis.hasMixedCurrencies ? (
            <div className="warning">
              В выборке несколько валют. Денежные итоги и общий процент эффективности не складываются; выберите одного клиента.
            </div>
          ) : null}

          <section className="budgets-layout">
            <article className="panel budgets-main">
              <div className="panel-head budgets-toolbar">
                <div className="session-controls budgets-filters">
                  <label>
                    Период
                    <select value={preset} onChange={(e) => setPreset(e.target.value as RangePreset)}>
                      <option value="qtd">Текущий квартал</option>
                      <option value="30">Последние 30 дней</option>
                      <option value="90">Последние 90 дней</option>
                    </select>
                  </label>
                  <label>
                    Клиент
                    <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
                      <option value="">Все клиенты</option>
                      {clients.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Статус
                    <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
                      <option value="active">Активные</option>
                      <option value="archived">Архив</option>
                      <option value="all">Все</option>
                    </select>
                  </label>
                </div>
                <div className="session-controls">
                  <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Клиент, аккаунт или ID" />
                  <button className="ghost-btn" onClick={() => void loadData()}>Применить</button>
                  <button className="ghost-btn" onClick={exportCsv}>Выгрузить CSV</button>
                </div>
              </div>

              <div className="budgets-table-wrap">
                <table className="budgets-table">
                  <thead>
                    <tr>
                      <th>Уровень</th>
                      <th>Клиент или аккаунт</th>
                      <th>Период</th>
                      <th>Бюджет</th>
                      <th>Расход</th>
                      <th>Темп</th>
                      <th>Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((r) => (
                      <tr
                        key={r.id || `${r.client_id}-${r.account_id || "client"}-${r.updated_at}`}
                        className={r.id === selectedBudgetId ? "selected" : ""}
                        onClick={() => setSelectedBudgetId(r.id || "")}
                      >
                        <td><span className={`badge scope-${r.scope}`}>{budgetScopeLabel(r.scope)}</span></td>
                        <td>
                          <div className="client-cell">
                            <div className="client-name">{r.resolvedAccountName || r.resolvedClientName}</div>
                            <div className="client-id">{r.resolvedAccountName ? r.resolvedClientName : `ID: ${r.client_id.slice(0, 8)}`}</div>
                          </div>
                        </td>
                        <td>{r.start_date || "--"} - {r.end_date || "--"}</td>
                        <td>{fmtMoney(Number(r.amount || 0), r.currency || "USD")}</td>
                        <td>
                          {r.usagePercent == null ? (
                            "--"
                          ) : (
                            <>
                              <div className={`usage-bar ${r.usagePercent >= 90 ? "high" : r.usagePercent >= 60 ? "mid" : "low"}`}>
                                <div style={{ width: `${Math.min(100, r.usagePercent)}%` }} />
                              </div>
                              {r.usagePercent.toFixed(1)}%
                            </>
                          )}
                        </td>
                        <td><span className={`badge ${statusClass(r.pace)}`}>{paceLabel(r.pace)}</span></td>
                        <td>{budgetStatusLabel(r.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="table-footer">
                <div className="session-controls">
                  <span className="muted-note">Строк на странице</span>
                  <select value={String(rowsPerPage)} onChange={(e) => setRowsPerPage(Number(e.target.value))}>
                    <option value="5">5</option>
                    <option value="10">10</option>
                    <option value="20">20</option>
                  </select>
                  <span className="muted-note">
                    Показано {rows.length ? (safePage - 1) * rowsPerPage + 1 : 0}–{Math.min(safePage * rowsPerPage, rows.length)} из {rows.length}
                  </span>
                </div>
                <div className="pager">
                  <button className="pager-btn" onClick={() => setPage((p) => Math.max(1, p - 1))}>‹</button>
                  <span className="pager-page">{safePage}</span>
                  <button className="pager-btn" onClick={() => setPage((p) => Math.min(pages, p + 1))}>›</button>
                </div>
              </div>
            </article>

            <aside className="panel budgets-detail">
              <div className="budgets-detail-head">
                <div>
                  <div className="kpi-title">Детали планового бюджета</div>
                  <h3>{selected?.resolvedAccountName || selected?.resolvedClientName || "Ничего не выбрано"}</h3>
                </div>
              </div>

              {!selected ? (
                <div className="muted-note">Выберите строку бюджета, чтобы посмотреть детали.</div>
              ) : (
                <>
                  <div className="detail-grid">
                    <div className="detail-item"><div className="detail-k">Статус</div><div className="detail-v">{budgetStatusLabel(selected.status)}</div></div>
                    <div className="detail-item"><div className="detail-k">Уровень</div><div className="detail-v">{budgetScopeLabel(selected.scope)}</div></div>
                    <div className="detail-item"><div className="detail-k">Период</div><div className="detail-v">{selected.start_date || "--"} - {selected.end_date || "--"}</div></div>
                    <div className="detail-item"><div className="detail-k">Версия</div><div className="detail-v">{selected.version || 1}</div></div>
                  </div>

                  <div className="panel budgets-detail-card">
                    <div className="kpi-title">Выделено</div>
                    <div className="budgets-money-line">
                      <strong>{fmtMoney(Number(selected.amount || 0), selected.currency || "USD")}</strong>
                      <span>Осталось {selected.usagePercent == null ? "--" : fmtMoney(Math.max(0, Number(selected.amount) - selected.spend), selected.currency || "USD")}</span>
                    </div>
                    <div className="usage-bar low">
                      <div style={{ width: `${Math.min(100, selected.usagePercent || 0)}%` }} />
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="action-row-head" style={{ marginBottom: 8 }}>
                      <h3 style={{ fontSize: 16, margin: 0 }}>История изменений</h3>
                      <div className="alert-actions" style={{ marginTop: 0 }}>
                        <button className={`mini-btn ${auditFilter === "all" ? "active" : ""}`} onClick={() => setAuditFilter("all")}>Все</button>
                        <button className={`mini-btn ${auditFilter === "transfers" ? "active" : ""}`} onClick={() => setAuditFilter("transfers")}>Переводы</button>
                        <button className={`mini-btn ${auditFilter === "notes" ? "active" : ""}`} onClick={() => setAuditFilter("notes")}>Заметки</button>
                      </div>
                    </div>
                    {(auditFilter === "all" || auditFilter === "transfers") ? (
                      <div className="alert-actions" style={{ marginTop: 0, marginBottom: 6 }}>
                        <button
                          className={`mini-btn ${transferDirection === "all" ? "active" : ""}`}
                          onClick={() => setTransferDirection("all")}
                        >
                          Все переводы
                        </button>
                        <button
                          className={`mini-btn ${transferDirection === "incoming" ? "active" : ""}`}
                          onClick={() => setTransferDirection("incoming")}
                        >
                          Входящие
                        </button>
                        <button
                          className={`mini-btn ${transferDirection === "outgoing" ? "active" : ""}`}
                          onClick={() => setTransferDirection("outgoing")}
                        >
                          Исходящие
                        </button>
                      </div>
                    ) : null}

                    {(auditFilter === "all" || auditFilter === "notes") ? (
                      <div className="activity-item">
                        <div className="activity-title">Состояние бюджета загружено</div>
                        <div className="activity-meta">{new Date(selected.updated_at).toLocaleString()}</div>
                      </div>
                    ) : null}
                    {(auditFilter === "all" || auditFilter === "notes") && selected.note ? (
                      <div className="activity-item">
                        <div className="activity-title">Заметка</div>
                        <div className="activity-meta">{selected.note}</div>
                      </div>
                    ) : null}
                    {(auditFilter === "all" || auditFilter === "transfers") ? (
                      transferHistoryLoading ? (
                        <div className="activity-item">
                          <div className="activity-meta">Загружаем историю переводов…</div>
                        </div>
                      ) : transferHistory.length ? (
                        transferHistory.slice(0, 6).map((t) => (
                          <div className="activity-item" key={t.id}>
                            <div className="activity-title">
                              Перевод {fmtMoney(Number(t.amount || 0), selected.currency || "USD")}
                            </div>
                            <div className="activity-meta">
                              {t.source_budget_id === selected.id ? "Исходящий" : "Входящий"} · {new Date(t.created_at).toLocaleString()}
                            </div>
                            {t.note ? <div className="activity-meta">{t.note}</div> : null}
                          </div>
                        ))
                      ) : (
                        <div className="activity-item">
                          <div className="activity-meta">Переводов пока нет.</div>
                        </div>
                      )
                    ) : null}
                  </div>

                  <div className="muted-note" style={{ marginTop: 10 }}>
                    Архивный бюджет скрывается из списка по умолчанию. Чтобы вернуть его, выберите фильтр «Архив» или «Все».
                  </div>
                  <div className={`budgets-action-status ${actionStatus ? "" : "hidden"}`} style={{ marginTop: 8 }}>
                    {actionStatus}
                  </div>

                  <div className="budgets-detail-actions">
                    <button className="primary-btn" onClick={() => void adjustSelected(1.1)} disabled={actionLoading || selected.status === "archived"}>
                      {actionLoading ? "Сохраняем…" : "Увеличить на 10%"}
                    </button>
                    <button className="ghost-btn" onClick={() => void adjustSelected(0.9)} disabled={actionLoading || selected.status === "archived"}>
                      {actionLoading ? "Сохраняем…" : "Уменьшить на 10%"}
                    </button>
                    <button
                      className="ghost-btn"
                      onClick={openTransferModal}
                      disabled={actionLoading || selected.status === "archived" || selected.scope !== "account"}
                    >
                      Перевести
                    </button>
                    {selected.status === "archived" ? (
                      <button className="ghost-btn" onClick={() => void restoreSelected()} disabled={actionLoading}>
                        {actionLoading ? "Сохраняем…" : "Восстановить"}
                      </button>
                    ) : (
                      <button
                        className="ghost-btn"
                        onClick={() => {
                          if (window.confirm("Переместить бюджет в архив? Он исчезнет из списка активных бюджетов.")) {
                            void archiveSelected();
                          }
                        }}
                        disabled={actionLoading}
                      >
                        {actionLoading ? "Сохраняем…" : "В архив"}
                      </button>
                    )}
                  </div>
                </>
              )}
            </aside>
          </section>

          <section className="budgets-mobile" aria-label="Бюджеты на мобильном устройстве">
            <div className="budgets-mobile-kpis">
              <div className="mobile-card"><div className="kpi-title">Выделено</div><div className="kpi-value">{totalBudgetLabel}</div></div>
              <div className="mobile-card"><div className="kpi-title">Расход</div><div className="kpi-value">{totalSpendLabel}</div></div>
              <div className="mobile-card"><div className="kpi-title">Использовано</div><div className="kpi-value">{efficiencyLabel}</div></div>
              <div className="mobile-card"><div className="kpi-title">Перерасход</div><div className="kpi-value">{kpis.atRisk}</div></div>
            </div>
            {rows.slice(0, 4).map((r) => (
              <article className="mobile-card" key={`m-${r.id}`}>
                <div className="mobile-card-head">
                  <div>
                    <div className="client-name">{r.resolvedAccountName || r.resolvedClientName}</div>
                    <div className="client-id">{r.resolvedClientName}</div>
                  </div>
                  <span className={`badge ${statusClass(r.pace)}`}>{paceLabel(r.pace)}</span>
                </div>
                <div className="panel-subtitle" style={{ marginTop: 8 }}>Расход</div>
                <div className="kpi-value" style={{ fontSize: 28 }}>{fmtMoney(r.spend, r.currency || "USD")}</div>
                <div className="usage-bar low"><div style={{ width: `${Math.min(100, r.usagePercent || 0)}%` }} /></div>
                <div className="alert-actions">
                  <button className="mini-btn" onClick={() => setSelectedBudgetId(r.id || "")}>Открыть</button>
                  <button className="mini-btn" onClick={() => push("История открыта в панели деталей", "info")}>История</button>
                </div>
              </article>
            ))}
            <div className="mobile-bottom-nav">
              <div className="mobile-nav-item">Обзор</div>
              <div className="mobile-nav-item" style={{ background: "#2f4666", color: "#fff" }}>Бюджеты</div>
              <div className="mobile-nav-item">Аналитика</div>
              <div className="mobile-nav-item">Настройки</div>
            </div>
            <button className="budgets-fab" onClick={openCreateModal}>+</button>
          </section>

          <ProviderBudgetControl
            apiBase={session.apiBase}
            token={session.token}
            clients={clients}
            accounts={accounts}
            role={agencyContext.role}
            agencyId={agencyContext.selectedAgencyId}
            agencyMemberRole={agencyContext.currentMember?.role}
            initialClientId={agencyContext.role === "solo_client" ? agencyContext.managedClientId : clientId}
          />
        </main>
      </div>

      <div
        className={`modal-backdrop ${createOpen ? "" : "hidden-view"}`}
        onClick={() => {
          if (!createLoading) setCreateOpen(false);
        }}
      >
        <div className="modal-card budgets-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-head">
            <div>
              <h3 style={{ margin: 0 }}>Новый плановый бюджет</h3>
              <div className="panel-subtitle">Задайте внутренний ориентир. Эта операция не меняет бюджет в рекламной платформе.</div>
            </div>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Закрыть</button>
          </div>
          <div className={`warning ${createError ? "" : "hidden"}`} style={{ marginTop: 10 }}>{createError}</div>
          <div className={`budgets-cap-hint ${createCapHint.level} ${createCapHint.text ? "" : "hidden"}`} style={{ marginTop: 8 }}>
            {createCapHint.loading ? "Проверяем…" : createCapHint.text}
          </div>
            <div className="detail-grid" style={{ marginTop: 10 }}>
            <label>
              Уровень
              <select
                value={createForm.scope}
                onChange={(e) => setCreateForm((s) => ({ ...s, scope: e.target.value as "client" | "account", account_id: "" }))}
              >
                <option value="client">Клиент</option>
                <option value="account">Аккаунт</option>
              </select>
            </label>
            <label>
              Клиент
              <select
                value={createForm.client_id}
                onChange={(e) => {
                  const nextClientId = e.target.value;
                  const clientCurrency = clients.find((client) => client.id === nextClientId)?.default_currency;
                  setCreateForm((s) => ({
                    ...s,
                    client_id: nextClientId,
                    account_id: "",
                    currency: String(clientCurrency || "USD").toUpperCase(),
                  }));
                }}
              >
                <option value="">Выберите клиента</option>
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>
            <label>
              Аккаунт
              <select
                value={createForm.account_id}
                disabled={createForm.scope !== "account"}
                onChange={(e) => {
                  const nextAccountId = e.target.value;
                  const accountCurrency = accounts.find((account) => account.id === nextAccountId)?.currency;
                  setCreateForm((s) => ({
                    ...s,
                    account_id: nextAccountId,
                    currency: String(accountCurrency || s.currency || "USD").toUpperCase(),
                  }));
                }}
              >
                <option value="">Выберите аккаунт</option>
                {accountsForClient.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label>
              Сумма
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={createForm.amount}
                onChange={(e) => setCreateForm((s) => ({ ...s, amount: e.target.value }))}
              />
            </label>
            <label>
              Валюта
              <input value={createForm.currency} onChange={(e) => setCreateForm((s) => ({ ...s, currency: e.target.value.toUpperCase() }))} />
            </label>
            <label>
              Тип периода
              <select
                value={createForm.period_type}
                onChange={(e) => setCreateForm((s) => ({ ...s, period_type: e.target.value as "monthly" | "custom" }))}
              >
                <option value="monthly">Месяц</option>
                <option value="custom">Произвольный период</option>
              </select>
            </label>
            <label>
              Дата начала
              <input type="date" value={createForm.start_date} onChange={(e) => setCreateForm((s) => ({ ...s, start_date: e.target.value }))} />
            </label>
            <label>
              Дата окончания
              <input type="date" value={createForm.end_date} onChange={(e) => setCreateForm((s) => ({ ...s, end_date: e.target.value }))} />
            </label>
          </div>
          <label style={{ display: "block", marginTop: 10 }}>
            Заметка
            <textarea value={createForm.note} onChange={(e) => setCreateForm((s) => ({ ...s, note: e.target.value }))} rows={3} style={{ width: "100%" }} />
          </label>
          {createForm.scope === "account" && createForm.client_id && accountsForClient.length === 0 ? (
            <div className="muted-note" style={{ marginTop: 8 }}>
              У выбранного клиента нет активных рекламных аккаунтов.
            </div>
          ) : null}
          <div className="session-controls" style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Отмена</button>
            <button className="primary-btn" disabled={!canCreate || createLoading || createCapBlocksSubmit} onClick={() => void createBudget()}>
              {createLoading ? "Создаём…" : "Создать плановый бюджет"}
            </button>
          </div>
        </div>
      </div>

      <div
        className={`modal-backdrop ${transferOpen ? "" : "hidden-view"}`}
        onClick={() => {
          if (!transferLoading) setTransferOpen(false);
        }}
      >
        <div className="modal-card budgets-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-head">
            <div>
              <h3 style={{ margin: 0 }}>Перевод планового бюджета</h3>
              <div className="panel-subtitle">Перенесите часть внутреннего лимита между аккаунтами. Настройки Meta и Google не изменятся.</div>
            </div>
            <button className="ghost-btn" onClick={() => setTransferOpen(false)} disabled={transferLoading}>Закрыть</button>
          </div>

          <div className={`warning ${transferError ? "" : "hidden"}`} style={{ marginTop: 10 }}>{transferError}</div>
          <div className="detail-grid" style={{ marginTop: 10 }}>
            <div className="detail-item">
              <div className="detail-k">Исходный бюджет</div>
              <div className="detail-v">{selected ? fmtMoney(Number(selected.amount || 0), selected.currency || "USD") : "--"}</div>
            </div>
            <div className="detail-item">
              <div className="detail-k">Можно перевести</div>
              <div className="detail-v">{selected ? fmtMoney(Number(selected.amount || 0), selected.currency || "USD") : "--"}</div>
            </div>
            <label>
              Аккаунт-получатель
              <select value={transferTargetAccountId} onChange={(e) => setTransferTargetAccountId(e.target.value)}>
                <option value="">Выберите аккаунт</option>
                {transferAccountOptions.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.platform})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Сумма
              <input
                type="number"
                min="0.01"
                step="0.01"
                max={selected ? Number(selected.amount || 0) : undefined}
                value={transferAmount}
                onChange={(e) => setTransferAmount(e.target.value)}
              />
            </label>
          </div>
          <div className="budgets-transfer-preview">
            <div className="detail-k">После перевода</div>
            <div className="budgets-transfer-row">
              <span>Источник</span>
              <strong>
                {selected ? `${fmtMoney(transferPreview?.sourceBefore || 0, selected.currency || "USD")} -> ${fmtMoney(transferPreview?.sourceAfter || 0, selected.currency || "USD")}` : "--"}
              </strong>
            </div>
            <div className="budgets-transfer-row">
              <span>Получатель</span>
              <strong>
                {selected ? `${fmtMoney(transferPreview?.targetBefore || 0, selected.currency || "USD")} -> ${fmtMoney(transferPreview?.targetAfter || 0, selected.currency || "USD")}` : "--"}
              </strong>
            </div>
            <div className={`budgets-transfer-hint ${(transferPreview?.validAmount && transferPreview?.validSource && transferPreview?.hasTarget) ? "ok" : "warn"}`}>
              {transferPreview?.hasTarget
                ? transferPreview?.validAmount
                  ? transferPreview?.validSource
                    ? "Перевод доступен. Итоговые суммы показаны выше."
                    : "Сумма перевода превышает исходный бюджет."
                  : "Введите сумму больше нуля."
                : "Выберите аккаунт-получатель, чтобы увидеть расчёт."}
            </div>
          </div>

          <div className="session-controls" style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <button className="ghost-btn" onClick={() => setTransferOpen(false)} disabled={transferLoading}>Отмена</button>
            <button className="primary-btn" onClick={() => void submitTransfer()} disabled={!canSubmitTransfer}>
              {transferLoading ? "Переводим…" : "Перевести"}
            </button>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
