"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppSidebar } from "../../components/AppSidebar";
import { AppTopTabs } from "../../components/AppTopTabs";
import { ToastHost } from "../../components/ToastHost";
import { useSession } from "../../hooks/useSession";
import { useToast } from "../../hooks/useToast";
import { fetchJson, getQuery } from "../../lib/api";
import { AdAccount, AgencyOverview, Budget, Client } from "../../lib/types";

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

function fmtMoney(v: number | null | undefined, currency = "USD") {
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(v || 0);
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
  if (pace === "overspending") return "OVESPENDING";
  if (pace === "underspending") return "UNDERSPENDING";
  if (pace === "on_track") return "ON TRACK";
  return "NO SIGNAL";
}

function buildCsv(rows: BudgetRow[]) {
  const head = ["scope", "client", "account", "budget", "usage_percent", "pace", "period_start", "period_end", "status"];
  const lines = rows.map((r) => [
    r.scope,
    r.resolvedClientName,
    r.resolvedAccountName || "",
    r.amount,
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
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const { session, setSession, persist, ready } = useSession(defaultApiBase);
  const { toasts, push } = useToast();

  const [warning, setWarning] = useState("");
  const [clients, setClients] = useState<Client[]>([]);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [agencyOverview, setAgencyOverview] = useState<AgencyOverview | null>(null);

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
    const range = rangeFromPreset(preset);
    const budgetQuery = getQuery({
      status,
      client_id: clientId || undefined,
      date_from: range.date_from,
      date_to: range.date_to,
    });
    const overviewQuery = getQuery({ date_from: range.date_from, date_to: range.date_to });
    const [c, a, b, agency] = await Promise.all([
      req<{ items: Client[] }>("/clients?status=active"),
      req<{ items: AdAccount[] }>("/ad-accounts?status=active"),
      req<{ items: Budget[] }>(`/budgets${budgetQuery}`),
      req<AgencyOverview>(`/agency/overview${overviewQuery}`),
    ]);
    setClients(c.items || []);
    setAccounts(a.items || []);
    setBudgets(b.items || []);
    setAgencyOverview(agency);
  }, [req, preset, status, clientId]);

  useEffect(() => {
    if (!ready || !session.token) return;
    void loadData().catch((err) => setWarning(err instanceof Error ? err.message : "Failed to load budgets"));
  }, [ready, session.token, loadData]);

  const clientMap = useMemo(() => new Map(clients.map((c) => [c.id, c.name])), [clients]);
  const accountMap = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);
  const spendByClient = useMemo(() => new Map((agencyOverview?.per_client || []).map((r) => [r.client_id, Number(r.spend || 0)])), [agencyOverview]);
  const spendByAccount = useMemo(
    () => new Map((agencyOverview?.per_account || []).map((r) => [r.account_id, Number(r.spend || 0)])),
    [agencyOverview]
  );

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const mapped = (budgets || []).map((b) => {
      const accountName = b.account_id ? accountMap.get(b.account_id)?.name || b.account_id : null;
      const spend = b.scope === "account" ? Number(spendByAccount.get(b.account_id || "") || 0) : Number(spendByClient.get(b.client_id) || 0);
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
  }, [budgets, search, clientMap, accountMap, spendByClient, spendByAccount]);

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

  const loadTransferHistory = useCallback(async (budgetId: string, direction: "all" | "incoming" | "outgoing") => {
    setTransferHistoryLoading(true);
    try {
      const rows = await req<BudgetTransferOut[]>(`/budgets/${budgetId}/transfers${getQuery({ direction, limit: 20 })}`);
      setTransferHistory(Array.isArray(rows) ? rows : []);
    } catch {
      setTransferHistory([]);
    } finally {
      setTransferHistoryLoading(false);
    }
  }, [req]);

  useEffect(() => {
    if (!selected?.id) {
      setTransferHistory([]);
      setTransferHistoryLoading(false);
      return;
    }
    void loadTransferHistory(selected.id, transferDirection);
  }, [selected?.id, transferDirection, loadTransferHistory]);

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
    const totalBudget = active.reduce((acc, x) => acc + Number(x.amount || 0), 0);
    const totalSpend = active.reduce((acc, x) => acc + Number(x.spend || 0), 0);
    const atRisk = active.filter((x) => x.pace === "overspending").length;
    return { activeBudgets: active.length, totalBudget, totalSpend, atRisk };
  }, [rows]);

  const parsedCreateAmount = Number(createForm.amount);
  const isAmountValid = Number.isFinite(parsedCreateAmount) && parsedCreateAmount > 0;
  const isDateRangeValid = !!createForm.start_date && !!createForm.end_date && createForm.start_date <= createForm.end_date;
  const isScopeValid = createForm.scope === "client" || !!createForm.account_id;
  const canCreate = !!createForm.client_id && isAmountValid && isDateRangeValid && isScopeValid;
  const createCapBlocksSubmit = createCapHint.level === "warn" && !!createCapHint.text;

  function openCreateModal() {
    setCreateError("");
    setCreateCapHint({ loading: false, level: "info", text: "" });
    setCreateForm(defaultCreateForm());
    setCreateOpen(true);
  }

  async function createBudget() {
    if (!isAmountValid) {
      setCreateError("Amount must be greater than 0.");
      return;
    }
    if (!isDateRangeValid) {
      setCreateError("End date must be on or after start date.");
      return;
    }
    if (!canCreate) {
      setCreateError("Fill all required fields before creating budget.");
      return;
    }
    if (createCapBlocksSubmit) {
      setCreateError("Fix cap rule conflict before submit.");
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
      push("Budget created", "success");
      setCreateOpen(false);
      setCreateForm(defaultCreateForm());
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Create budget failed";
      setWarning(msg);
      setCreateError(msg);
      push(msg, "error");
    } finally {
      setCreateLoading(false);
    }
  }

  async function adjustSelected(deltaFactor: number) {
    if (!selected?.id) {
      const msg = "Selected budget has no id. Reload list and try again.";
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
      const msg = `Allocation updated to ${fmtMoney(nextAmount, selected.currency || "USD")}`;
      setActionStatus(msg);
      push(msg, "success");
      await loadData();
      await loadTransferHistory(selected.id, transferDirection);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Adjust allocation failed";
      setWarning(msg);
      setActionStatus(`Adjust failed: ${msg}`);
      push(msg, "error");
    } finally {
      setActionLoading(false);
    }
  }

  async function archiveSelected() {
    if (!selected?.id) {
      const msg = "Selected budget has no id. Reload list and try again.";
      setWarning(msg);
      push(msg, "error");
      return;
    }
    try {
      setActionLoading(true);
      setActionStatus("");
      await req<Budget>(`/budgets/${selected.id}`, { method: "DELETE" });
      const msg = "Budget archived. To restore, switch Status filter to Archived/All and click Restore.";
      setActionStatus(msg);
      push("Budget archived", "success");
      await loadData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Archive failed";
      setWarning(msg);
      setActionStatus(`Archive failed: ${msg}`);
      push(msg, "error");
    } finally {
      setActionLoading(false);
    }
  }

  async function restoreSelected() {
    if (!selected?.id) {
      const msg = "Selected budget has no id. Reload list and try again.";
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
      const msg = "Budget restored to active.";
      setActionStatus(msg);
      push(msg, "success");
      await loadData();
      await loadTransferHistory(selected.id, transferDirection);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Restore failed";
      setWarning(msg);
      setActionStatus(`Restore failed: ${msg}`);
      push(msg, "error");
    } finally {
      setActionLoading(false);
    }
  }

  function openTransferModal() {
    if (!selected || selected.scope !== "account" || !selected.account_id || selected.status !== "active") return;
    const preferredAmount = Math.max(0, Math.min(100, Number(selected.amount || 0)));
    setTransferError("");
    setTransferTargetAccountId("");
    setTransferAmount(preferredAmount > 0 ? preferredAmount.toFixed(2) : "");
    setTransferOpen(true);
  }

  async function submitTransfer() {
    if (!selected?.id || selected.scope !== "account" || !selected.account_id) {
      setTransferError("Select an active account budget first.");
      return;
    }
    const amount = Number(transferAmount);
    if (!transferTargetAccountId) {
      setTransferError("Select target account.");
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setTransferError("Transfer amount must be greater than 0.");
      return;
    }
    if (amount > Number(selected.amount || 0)) {
      setTransferError("Transfer amount exceeds source budget.");
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
      const msg = `Transferred ${fmtMoney(Number(res.transferred_amount || amount), selected.currency || "USD")}`;
      setActionStatus(msg);
      push(msg, "success");
      setTransferOpen(false);
      await loadData();
      await loadTransferHistory(selected.id, transferDirection);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Transfer failed";
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
    push("CSV exported", "info");
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

      setCreateCapHint({ loading: true, level: "info", text: "Checking budget cap..." });
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
              text: "No active client budget for this period. Server cap check still applies.",
            });
            return;
          }
          const clientCap = Number(clientBudget.amount || 0);
          const projected = accountSum + amount;
          if (projected > clientCap) {
            setCreateCapHint({
              loading: false,
              level: "warn",
              text: `Projected account allocations ${fmtMoney(projected)} exceed client cap ${fmtMoney(clientCap)}.`,
            });
          } else {
            setCreateCapHint({
              loading: false,
              level: "ok",
              text: `Projected account allocations ${fmtMoney(projected)} / client cap ${fmtMoney(clientCap)}.`,
            });
          }
          return;
        }

        if (amount < accountSum) {
          setCreateCapHint({
            loading: false,
            level: "warn",
            text: `Client budget ${fmtMoney(amount)} is lower than allocated account budgets ${fmtMoney(accountSum)}.`,
          });
        } else {
          setCreateCapHint({
            loading: false,
            level: "ok",
            text: `Client budget ${fmtMoney(amount)} covers account allocations ${fmtMoney(accountSum)}.`,
          });
        }
      } catch {
        if (!cancelled) {
          setCreateCapHint({
            loading: false,
            level: "info",
            text: "Cap pre-check unavailable. Server validation will run on submit.",
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
        <AppSidebar active="budgets" subtitle="Financial Control" className="sidebar budgets-sidebar" />

        <main className="content budgets-content">
          <header className="topbar budgets-topbar">
            <div className="topbar-left">
              <AppTopTabs active="budgets" />
              <div className="topbar-title">Accounts Ledger</div>
              <div className="panel-subtitle">Precision tracking for active organizational allocations.</div>
            </div>
            <div className="session-controls">
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
                    await loadData();
                    push("Session saved", "success");
                  } catch (err) {
                    setWarning(err instanceof Error ? err.message : "Load failed");
                  }
                }}
                disabled={!ready}
              >
                Save
              </button>
              <button className="primary-btn" onClick={openCreateModal}>Create Budget</button>
            </div>
          </header>

          <div className={`warning ${warning ? "" : "hidden"}`}>{warning}</div>

          <section className="kpi-grid budgets-kpis">
            <article className="kpi-card">
              <div className="kpi-title">Active Budgets</div>
              <div className="kpi-value">{kpis.activeBudgets}</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-title">Total Budget</div>
              <div className="kpi-value">{fmtMoney(kpis.totalBudget)}</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-title">Total Spend</div>
              <div className="kpi-value">{fmtMoney(kpis.totalSpend)}</div>
            </article>
            <article className="kpi-card bad">
              <div className="kpi-title">At Risk</div>
              <div className="kpi-value">{kpis.atRisk}</div>
            </article>
          </section>

          <section className="budgets-layout">
            <article className="panel budgets-main">
              <div className="panel-head budgets-toolbar">
                <div className="session-controls budgets-filters">
                  <label>
                    Range
                    <select value={preset} onChange={(e) => setPreset(e.target.value as RangePreset)}>
                      <option value="qtd">Current Quarter</option>
                      <option value="30">Last 30 Days</option>
                      <option value="90">Last 90 Days</option>
                    </select>
                  </label>
                  <label>
                    Client
                    <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
                      <option value="">All Clients</option>
                      {clients.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Status
                    <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
                      <option value="active">Active</option>
                      <option value="archived">Archived</option>
                      <option value="all">All</option>
                    </select>
                  </label>
                </div>
                <div className="session-controls">
                  <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search client/account/id" />
                  <button className="ghost-btn" onClick={() => void loadData()}>Apply Filters</button>
                  <button className="ghost-btn" onClick={exportCsv}>Export</button>
                </div>
              </div>

              <div className="budgets-table-wrap">
                <table className="budgets-table">
                  <thead>
                    <tr>
                      <th>Scope</th>
                      <th>Account Name</th>
                      <th>Period</th>
                      <th>Budget</th>
                      <th>Usage</th>
                      <th>Pace</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((r) => (
                      <tr
                        key={r.id || `${r.client_id}-${r.account_id || "client"}-${r.updated_at}`}
                        className={r.id === selectedBudgetId ? "selected" : ""}
                        onClick={() => setSelectedBudgetId(r.id || "")}
                      >
                        <td><span className={`badge scope-${r.scope}`}>{r.scope.toUpperCase()}</span></td>
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
                        <td>{r.status || "active"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="table-footer">
                <div className="session-controls">
                  <span className="muted-note">Rows per page</span>
                  <select value={String(rowsPerPage)} onChange={(e) => setRowsPerPage(Number(e.target.value))}>
                    <option value="5">5</option>
                    <option value="10">10</option>
                    <option value="20">20</option>
                  </select>
                  <span className="muted-note">
                    Showing {rows.length ? (safePage - 1) * rowsPerPage + 1 : 0}-{Math.min(safePage * rowsPerPage, rows.length)} of {rows.length}
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
                  <div className="kpi-title">Detail Panel</div>
                  <h3>{selected?.resolvedAccountName || selected?.resolvedClientName || "No selection"}</h3>
                </div>
              </div>

              {!selected ? (
                <div className="muted-note">Select a budget row to inspect details.</div>
              ) : (
                <>
                  <div className="detail-grid">
                    <div className="detail-item"><div className="detail-k">Status</div><div className="detail-v">{selected.status || "active"}</div></div>
                    <div className="detail-item"><div className="detail-k">Scope</div><div className="detail-v">{selected.scope}</div></div>
                    <div className="detail-item"><div className="detail-k">Duration</div><div className="detail-v">{selected.start_date || "--"} - {selected.end_date || "--"}</div></div>
                    <div className="detail-item"><div className="detail-k">Version</div><div className="detail-v">{selected.version || 1}</div></div>
                  </div>

                  <div className="panel budgets-detail-card">
                    <div className="kpi-title">Total Allocated</div>
                    <div className="budgets-money-line">
                      <strong>{fmtMoney(Number(selected.amount || 0), selected.currency || "USD")}</strong>
                      <span>Remaining {selected.usagePercent == null ? "--" : fmtMoney(Math.max(0, Number(selected.amount) - selected.spend), selected.currency || "USD")}</span>
                    </div>
                    <div className="usage-bar low">
                      <div style={{ width: `${Math.min(100, selected.usagePercent || 0)}%` }} />
                    </div>
                  </div>

                  <div className="panel" style={{ marginTop: 10 }}>
                    <div className="action-row-head" style={{ marginBottom: 8 }}>
                      <h3 style={{ fontSize: 16, margin: 0 }}>Audit Trail</h3>
                      <div className="alert-actions" style={{ marginTop: 0 }}>
                        <button className={`mini-btn ${auditFilter === "all" ? "active" : ""}`} onClick={() => setAuditFilter("all")}>All</button>
                        <button className={`mini-btn ${auditFilter === "transfers" ? "active" : ""}`} onClick={() => setAuditFilter("transfers")}>Transfers</button>
                        <button className={`mini-btn ${auditFilter === "notes" ? "active" : ""}`} onClick={() => setAuditFilter("notes")}>Notes</button>
                      </div>
                    </div>
                    {(auditFilter === "all" || auditFilter === "transfers") ? (
                      <div className="alert-actions" style={{ marginTop: 0, marginBottom: 6 }}>
                        <button
                          className={`mini-btn ${transferDirection === "all" ? "active" : ""}`}
                          onClick={() => setTransferDirection("all")}
                        >
                          All Transfers
                        </button>
                        <button
                          className={`mini-btn ${transferDirection === "incoming" ? "active" : ""}`}
                          onClick={() => setTransferDirection("incoming")}
                        >
                          Incoming
                        </button>
                        <button
                          className={`mini-btn ${transferDirection === "outgoing" ? "active" : ""}`}
                          onClick={() => setTransferDirection("outgoing")}
                        >
                          Outgoing
                        </button>
                      </div>
                    ) : null}

                    {(auditFilter === "all" || auditFilter === "notes") ? (
                      <div className="activity-item">
                        <div className="activity-title">Budget snapshot loaded</div>
                        <div className="activity-meta">{new Date(selected.updated_at).toLocaleString()}</div>
                      </div>
                    ) : null}
                    {(auditFilter === "all" || auditFilter === "notes") && selected.note ? (
                      <div className="activity-item">
                        <div className="activity-title">Note</div>
                        <div className="activity-meta">{selected.note}</div>
                      </div>
                    ) : null}
                    {(auditFilter === "all" || auditFilter === "transfers") ? (
                      transferHistoryLoading ? (
                        <div className="activity-item">
                          <div className="activity-meta">Loading transfer history...</div>
                        </div>
                      ) : transferHistory.length ? (
                        transferHistory.slice(0, 6).map((t) => (
                          <div className="activity-item" key={t.id}>
                            <div className="activity-title">
                              Transfer {fmtMoney(Number(t.amount || 0), selected.currency || "USD")}
                            </div>
                            <div className="activity-meta">
                              {t.source_budget_id === selected.id ? "Outgoing" : "Incoming"} · {new Date(t.created_at).toLocaleString()}
                            </div>
                            {t.note ? <div className="activity-meta">{t.note}</div> : null}
                          </div>
                        ))
                      ) : (
                        <div className="activity-item">
                          <div className="activity-meta">No transfers yet.</div>
                        </div>
                      )
                    ) : null}
                  </div>

                  <div className="muted-note" style={{ marginTop: 10 }}>
                    Archive hides budget from default list (`status=active`). Use `Archived` or `All` filter to restore later.
                  </div>
                  <div className={`budgets-action-status ${actionStatus ? "" : "hidden"}`} style={{ marginTop: 8 }}>
                    {actionStatus}
                  </div>

                  <div className="budgets-detail-actions">
                    <button className="primary-btn" onClick={() => void adjustSelected(1.1)} disabled={actionLoading || selected.status === "archived"}>
                      {actionLoading ? "Saving..." : "Adjust +10%"}
                    </button>
                    <button className="ghost-btn" onClick={() => void adjustSelected(0.9)} disabled={actionLoading || selected.status === "archived"}>
                      {actionLoading ? "Saving..." : "Adjust -10%"}
                    </button>
                    <button
                      className="ghost-btn"
                      onClick={openTransferModal}
                      disabled={actionLoading || selected.status === "archived" || selected.scope !== "account"}
                    >
                      Transfer
                    </button>
                    {selected.status === "archived" ? (
                      <button className="ghost-btn" onClick={() => void restoreSelected()} disabled={actionLoading}>
                        {actionLoading ? "Saving..." : "Restore"}
                      </button>
                    ) : (
                      <button
                        className="ghost-btn"
                        onClick={() => {
                          if (window.confirm("Archive this budget? It will disappear from default active list.")) {
                            void archiveSelected();
                          }
                        }}
                        disabled={actionLoading}
                      >
                        {actionLoading ? "Saving..." : "Archive Budget"}
                      </button>
                    )}
                  </div>
                </>
              )}
            </aside>
          </section>

          <section className="budgets-mobile" aria-label="mobile budgets view">
            <div className="budgets-mobile-kpis">
              <div className="mobile-card"><div className="kpi-title">Total Allocated</div><div className="kpi-value">{fmtMoney(kpis.totalBudget)}</div></div>
              <div className="mobile-card"><div className="kpi-title">Active Burn</div><div className="kpi-value">{fmtMoney(kpis.totalSpend)}</div></div>
              <div className="mobile-card"><div className="kpi-title">Efficiency</div><div className="kpi-value">{kpis.totalBudget > 0 ? `${((kpis.totalSpend / kpis.totalBudget) * 100).toFixed(1)}%` : "--"}</div></div>
              <div className="mobile-card"><div className="kpi-title">Over Pace</div><div className="kpi-value">{kpis.atRisk}</div></div>
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
                <div className="panel-subtitle" style={{ marginTop: 8 }}>Spend</div>
                <div className="kpi-value" style={{ fontSize: 28 }}>{fmtMoney(r.spend, r.currency || "USD")}</div>
                <div className="usage-bar low"><div style={{ width: `${Math.min(100, r.usagePercent || 0)}%` }} /></div>
                <div className="alert-actions">
                  <button className="mini-btn" onClick={() => setSelectedBudgetId(r.id || "")}>Edit</button>
                  <button className="mini-btn" onClick={() => push("History opened in detail panel", "info")}>History</button>
                </div>
              </article>
            ))}
            <div className="mobile-bottom-nav">
              <div className="mobile-nav-item">Overview</div>
              <div className="mobile-nav-item" style={{ background: "#2f4666", color: "#fff" }}>Budgets</div>
              <div className="mobile-nav-item">Analysis</div>
              <div className="mobile-nav-item">Settings</div>
            </div>
            <button className="budgets-fab" onClick={openCreateModal}>+</button>
          </section>
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
              <h3 style={{ margin: 0 }}>Create Budget</h3>
              <div className="panel-subtitle">Manual budget entry (client/account scope)</div>
            </div>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Close</button>
          </div>
          <div className={`warning ${createError ? "" : "hidden"}`} style={{ marginTop: 10 }}>{createError}</div>
          <div className={`budgets-cap-hint ${createCapHint.level} ${createCapHint.text ? "" : "hidden"}`} style={{ marginTop: 8 }}>
            {createCapHint.loading ? "Checking..." : createCapHint.text}
          </div>
            <div className="detail-grid" style={{ marginTop: 10 }}>
            <label>
              Scope
              <select
                value={createForm.scope}
                onChange={(e) => setCreateForm((s) => ({ ...s, scope: e.target.value as "client" | "account", account_id: "" }))}
              >
                <option value="client">Client</option>
                <option value="account">Account</option>
              </select>
            </label>
            <label>
              Client
              <select value={createForm.client_id} onChange={(e) => setCreateForm((s) => ({ ...s, client_id: e.target.value, account_id: "" }))}>
                <option value="">Select client</option>
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>
            <label>
              Account
              <select
                value={createForm.account_id}
                disabled={createForm.scope !== "account"}
                onChange={(e) => setCreateForm((s) => ({ ...s, account_id: e.target.value }))}
              >
                <option value="">Select account</option>
                {accountsForClient.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label>
              Amount
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={createForm.amount}
                onChange={(e) => setCreateForm((s) => ({ ...s, amount: e.target.value }))}
              />
            </label>
            <label>
              Currency
              <input value={createForm.currency} onChange={(e) => setCreateForm((s) => ({ ...s, currency: e.target.value.toUpperCase() }))} />
            </label>
            <label>
              Period Type
              <select
                value={createForm.period_type}
                onChange={(e) => setCreateForm((s) => ({ ...s, period_type: e.target.value as "monthly" | "custom" }))}
              >
                <option value="monthly">monthly</option>
                <option value="custom">custom</option>
              </select>
            </label>
            <label>
              Start Date
              <input type="date" value={createForm.start_date} onChange={(e) => setCreateForm((s) => ({ ...s, start_date: e.target.value }))} />
            </label>
            <label>
              End Date
              <input type="date" value={createForm.end_date} onChange={(e) => setCreateForm((s) => ({ ...s, end_date: e.target.value }))} />
            </label>
          </div>
          <label style={{ display: "block", marginTop: 10 }}>
            Note
            <textarea value={createForm.note} onChange={(e) => setCreateForm((s) => ({ ...s, note: e.target.value }))} rows={3} style={{ width: "100%" }} />
          </label>
          {createForm.scope === "account" && createForm.client_id && accountsForClient.length === 0 ? (
            <div className="muted-note" style={{ marginTop: 8 }}>
              No active ad accounts for selected client.
            </div>
          ) : null}
          <div className="session-controls" style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <button className="ghost-btn" onClick={() => setCreateOpen(false)} disabled={createLoading}>Cancel</button>
            <button className="primary-btn" disabled={!canCreate || createLoading || createCapBlocksSubmit} onClick={() => void createBudget()}>
              {createLoading ? "Creating..." : "Create Budget"}
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
              <h3 style={{ margin: 0 }}>Transfer Budget</h3>
              <div className="panel-subtitle">Move amount from selected source account budget to another account.</div>
            </div>
            <button className="ghost-btn" onClick={() => setTransferOpen(false)} disabled={transferLoading}>Close</button>
          </div>

          <div className={`warning ${transferError ? "" : "hidden"}`} style={{ marginTop: 10 }}>{transferError}</div>
          <div className="detail-grid" style={{ marginTop: 10 }}>
            <div className="detail-item">
              <div className="detail-k">Source Budget</div>
              <div className="detail-v">{selected ? fmtMoney(Number(selected.amount || 0), selected.currency || "USD") : "--"}</div>
            </div>
            <div className="detail-item">
              <div className="detail-k">Max Transfer</div>
              <div className="detail-v">{selected ? fmtMoney(Number(selected.amount || 0), selected.currency || "USD") : "--"}</div>
            </div>
            <label>
              Target Account
              <select value={transferTargetAccountId} onChange={(e) => setTransferTargetAccountId(e.target.value)}>
                <option value="">Select target account</option>
                {transferAccountOptions.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.platform})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Amount
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
            <div className="detail-k">Transfer Preview</div>
            <div className="budgets-transfer-row">
              <span>Source</span>
              <strong>
                {selected ? `${fmtMoney(transferPreview?.sourceBefore || 0, selected.currency || "USD")} -> ${fmtMoney(transferPreview?.sourceAfter || 0, selected.currency || "USD")}` : "--"}
              </strong>
            </div>
            <div className="budgets-transfer-row">
              <span>Target</span>
              <strong>
                {selected ? `${fmtMoney(transferPreview?.targetBefore || 0, selected.currency || "USD")} -> ${fmtMoney(transferPreview?.targetAfter || 0, selected.currency || "USD")}` : "--"}
              </strong>
            </div>
            <div className={`budgets-transfer-hint ${(transferPreview?.validAmount && transferPreview?.validSource && transferPreview?.hasTarget) ? "ok" : "warn"}`}>
              {transferPreview?.hasTarget
                ? transferPreview?.validAmount
                  ? transferPreview?.validSource
                    ? "Transfer is valid. Source/target projections shown above."
                    : "Invalid: transfer amount exceeds source budget."
                  : "Enter transfer amount greater than 0."
                : "Select target account to preview transfer."}
            </div>
          </div>

          <div className="session-controls" style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <button className="ghost-btn" onClick={() => setTransferOpen(false)} disabled={transferLoading}>Cancel</button>
            <button className="primary-btn" onClick={() => void submitTransfer()} disabled={!canSubmitTransfer}>
              {transferLoading ? "Transferring..." : "Transfer"}
            </button>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </>
  );
}
