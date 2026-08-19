"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiRequestError, fetchJson, getQuery } from "../lib/api";
import {
  META_BUDGET_STATUS_META,
  MetaBudgetChangeRequest,
  MetaBudgetCommand,
  MetaBudgetCommandResponse,
  MetaBudgetField,
  MetaBudgetHistoryResponse,
  MetaBudgetPreview,
  MetaBudgetReadiness,
  MetaBudgetTarget,
  MetaBudgetTargetField,
  MetaBudgetTargetsResponse,
  MetaBudgetTargetType,
  MetaBudgetWarning,
  currencyFractionDigits,
  formatMinorMoney,
  majorInputToMinor,
  makeIdempotencyKey,
  metaBudgetFieldLabel,
  metaBudgetTargetLabel,
  minorToInput,
} from "../lib/providerBudget";
import type { AdAccount, Client } from "../lib/types";

type ProviderBudgetRole = "admin" | "agency" | "client" | "solo_client" | null;
type AgencyMemberRole = "owner" | "manager" | "member" | null | undefined;

type ProviderBudgetControlProps = {
  apiBase: string;
  token?: string;
  clients: Array<Pick<Client, "id" | "name" | "default_currency">>;
  accounts: AdAccount[];
  role: ProviderBudgetRole;
  agencyId?: string | null;
  agencyMemberRole?: AgencyMemberRole;
  initialClientId?: string | null;
  compact?: boolean;
};

type TargetChoice = {
  key: string;
  target: MetaBudgetTarget;
  budget: MetaBudgetTargetField;
};

function isTargetType(value: unknown): value is MetaBudgetTargetType {
  return value === "campaign" || value === "ad_set";
}

function isBudgetField(value: unknown): value is MetaBudgetField {
  return value === "daily_budget" || value === "lifetime_budget";
}

function normalizeReadiness(value: MetaBudgetReadiness): MetaBudgetReadiness | null {
  if (!value || value.provider !== "meta" || typeof value.visible !== "boolean") return null;
  return {
    ...value,
    feature_enabled: value.feature_enabled === true,
    visible: value.visible === true,
    can_read_history: value.can_read_history === true,
    can_preview: value.can_preview === true,
    can_confirm: value.can_confirm === true,
    can_reconcile: value.can_reconcile === true,
    credential_ready: value.credential_ready === true,
    binding_ready: value.binding_ready === true,
  };
}

function normalizeTargets(value: MetaBudgetTargetsResponse): MetaBudgetTarget[] {
  if (!value || !Array.isArray(value.items)) return [];
  const rows: MetaBudgetTarget[] = [];
  for (const item of value.items) {
    if (!item || !isTargetType(item.target_type) || !String(item.provider_target_id || "").match(/^[0-9]+$/)) continue;
    const fields = Array.isArray(item.budget_fields)
      ? item.budget_fields.filter((field): field is MetaBudgetTargetField => (
          !!field
          && isBudgetField(field.field)
          && Number.isSafeInteger(field.current_minor)
          && field.current_minor >= 0
          && /^[A-Z]{3}$/.test(String(field.currency || ""))
        ))
      : [];
    if (!fields.length) continue;
    rows.push({
      target_type: item.target_type,
      provider_target_id: item.provider_target_id,
      name: String(item.name || `${metaBudgetTargetLabel(item.target_type)} ${item.provider_target_id}`),
      status: item.status ? String(item.status) : null,
      budget_fields: fields,
    });
  }
  return rows;
}

function normalizedCommand(value: unknown): MetaBudgetCommand | null {
  const candidate = (
    value && typeof value === "object" && "command" in value
      ? (value as { command?: unknown }).command
      : value
  ) as MetaBudgetCommand | null;
  if (!candidate || typeof candidate !== "object") return null;
  if (!String(candidate.id || "") || !(candidate.status in META_BUDGET_STATUS_META)) return null;
  if (!candidate.request || !isTargetType(candidate.request.target_type) || !isBudgetField(candidate.request.field)) return null;
  return candidate;
}

function normalizeHistory(value: MetaBudgetHistoryResponse): MetaBudgetCommand[] {
  if (!value || !Array.isArray(value.items)) return [];
  return value.items.map((item) => normalizedCommand(item)).filter((item): item is MetaBudgetCommand => !!item);
}

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

const META_PERMISSION_CODES = new Set([
  "meta_permission_required",
  "meta_permissions_missing",
  "meta_budget_ads_management_missing",
  "credential_permission_missing",
]);

const META_RECONNECT_CODES = new Set([
  ...META_PERMISSION_CODES,
  "meta_access_token_missing",
  "meta_connection_unverified",
  "meta_token_app_mismatch",
  "meta_business_config_mismatch",
  "meta_token_user_missing",
  "meta_token_expiry_missing",
  "meta_token_expired",
  "meta_budget_credential_not_ready",
  "meta_budget_credential_incomplete",
  "meta_budget_rediscovery_required",
]);

function isMetaPermissionProblem(readiness: MetaBudgetReadiness): boolean {
  return META_PERMISSION_CODES.has(String(readiness.reason_code || ""));
}

function requiresMetaReconnect(readiness: MetaBudgetReadiness): boolean {
  return META_RECONNECT_CODES.has(String(readiness.reason_code || ""));
}

function reasonText(readiness: MetaBudgetReadiness): string {
  const code = String(readiness.reason_code || "");
  if (isMetaPermissionProblem(readiness)) {
    return "Переподключите Meta и разрешите управление рекламными бюджетами.";
  }
  if (!readiness.feature_enabled) return "Управление бюджетами Meta пока выключено для этого контура.";
  if (code === "meta_budget_client_not_allowed") return "Управление бюджетами ещё не включено для этого клиента.";
  if (code === "client_not_active") return "Клиент неактивен. Новые изменения бюджета недоступны.";
  if (code === "meta_budget_assignment_conflict") return "У аккаунта конфликт назначения клиенту. Сначала исправьте владельца аккаунта.";
  if (code === "meta_budget_credential_storage_not_ready") return "Подключение Meta должен безопасно обновить администратор платформы.";
  if (code === "meta_budget_scope_inactive") return "Клиент или рекламный аккаунт Meta неактивен.";
  if (code === "meta_budget_write_scope_not_ready") return "Текущая роль или подключение не разрешает менять бюджеты в Meta.";
  if (code === "client_read_only") return "Клиентский доступ позволяет только просматривать значения и историю.";
  if (requiresMetaReconnect(readiness)) return "Переподключите Meta, чтобы снова управлять рекламными бюджетами.";
  if (code === "meta_budget_preview_secret_missing"
    || code === "meta_budget_preview_ttl_invalid"
    || code === "meta_budget_provider_configuration_missing"
    || code === "meta_budget_rollout_configuration_invalid") {
    return "Управление бюджетами временно недоступно из-за настройки платформы. Обратитесь к администратору.";
  }
  if (!readiness.binding_ready) return "Рекламный аккаунт ещё не привязан к подтверждённому подключению Meta.";
  if (!readiness.credential_ready) return "Подключение Meta сейчас не готово к чтению и изменению бюджетов.";
  if (!readiness.can_confirm) return "Текущая роль может просматривать историю, но не может менять бюджеты в Meta.";
  if (readiness.message) return readiness.message;
  return "Управление бюджетами Meta сейчас недоступно.";
}

function requestFingerprint(request: MetaBudgetChangeRequest): string {
  return JSON.stringify([
    request.client_id,
    request.ad_account_id,
    request.agency_id || "",
    request.target_type,
    request.provider_target_id,
    request.field,
    request.amount_minor,
    request.expected_current_minor,
    request.currency,
    request.reason,
  ]);
}

function calculatedWarnings(preview: MetaBudgetPreview, loadedCurrent: number): MetaBudgetWarning[] {
  const rows = Array.isArray(preview.warnings) ? [...preview.warnings] : [];
  if (preview.current_minor !== loadedCurrent) {
    rows.unshift({
      code: "provider_value_changed",
      severity: "critical",
      message: "Текущее значение в Meta изменилось после загрузки списка. Проверьте обновлённую сумму перед подтверждением.",
    });
  }
  if (preview.delta_minor === 0) {
    rows.push({
      code: "no_change",
      severity: "info",
      message: "Новое значение совпадает с текущим — отправлять изменение не требуется.",
    });
  } else if (preview.current_minor > 0 && Math.abs(preview.delta_minor) / preview.current_minor >= 0.5) {
    rows.push({
      code: "large_delta",
      severity: "warning",
      message: "Изменение составляет 50% или больше. Ещё раз проверьте сумму и выбранную кампанию.",
    });
  }
  return rows.filter((warning, index, all) => all.findIndex((other) => other.code === warning.code) === index);
}

function roleCanAttemptWrite(role: ProviderBudgetRole, agencyMemberRole: AgencyMemberRole): boolean {
  if (role === "admin" || role === "solo_client") return true;
  if (role === "agency") return agencyMemberRole === "owner" || agencyMemberRole === "manager";
  return false;
}

export function ProviderBudgetControl({
  apiBase,
  token,
  clients,
  accounts,
  role,
  agencyId,
  agencyMemberRole,
  initialClientId,
  compact = false,
}: ProviderBudgetControlProps) {
  const metaAccounts = useMemo(
    () => accounts.filter((account) => account.platform.toLowerCase() === "meta" && account.status === "active"),
    [accounts],
  );
  // Keep clients without an active Meta account selectable: their immutable
  // command history must remain available after an account is archived or a
  // connection expires.
  const availableClients = clients;

  const preferredClientId = useMemo(() => {
    if (initialClientId && availableClients.some((client) => client.id === initialClientId)) return initialClientId;
    if (role === "solo_client" || role === "client" || availableClients.length === 1) return availableClients[0]?.id || "";
    return availableClients[0]?.id || "";
  }, [availableClients, initialClientId, role]);

  const [selectedClientId, setSelectedClientId] = useState("");
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [readiness, setReadiness] = useState<MetaBudgetReadiness | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(false);
  const [targets, setTargets] = useState<MetaBudgetTarget[]>([]);
  const [targetsLoading, setTargetsLoading] = useState(false);
  const [targetsError, setTargetsError] = useState("");
  const [targetKey, setTargetKey] = useState("");
  const [amountInput, setAmountInput] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<MetaBudgetPreview | null>(null);
  const [previewWarnings, setPreviewWarnings] = useState<MetaBudgetWarning[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [submittedCommand, setSubmittedCommand] = useState<MetaBudgetCommand | null>(null);
  const [history, setHistory] = useState<MetaBudgetCommand[]>([]);
  const [historyClientId, setHistoryClientId] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyErrorClientId, setHistoryErrorClientId] = useState("");
  const [selectedCommandId, setSelectedCommandId] = useState("");
  const [checkingCommandId, setCheckingCommandId] = useState("");
  const [resolvingCommandId, setResolvingCommandId] = useState("");
  const [resolveConfirmedCommandId, setResolveConfirmedCommandId] = useState("");
  const [resolveRetryAt, setResolveRetryAt] = useState<{ commandId: string; timestamp: number } | null>(null);
  const [commandActionError, setCommandActionError] = useState("");
  const [clock, setClock] = useState(() => Date.now());
  const selectedClientRef = useRef("");
  const historyRequestGeneration = useRef(0);
  const historyAbortRef = useRef<AbortController | null>(null);
  const previewRequestGeneration = useRef(0);
  const previewAbortRef = useRef<AbortController | null>(null);
  const draftFingerprintRef = useRef("");

  selectedClientRef.current = selectedClientId;

  const req = useCallback(
    <T,>(path: string, init?: RequestInit) => fetchJson<T>(apiBase, path, token, init),
    [apiBase, token],
  );

  useEffect(() => () => {
    historyAbortRef.current?.abort();
    previewAbortRef.current?.abort();
  }, []);

  useEffect(() => {
    setSelectedClientId((current) => (
      availableClients.some((client) => client.id === current) ? current : preferredClientId
    ));
  }, [availableClients, preferredClientId]);

  const accountsForClient = useMemo(
    () => metaAccounts.filter((account) => account.client_id === selectedClientId),
    [metaAccounts, selectedClientId],
  );

  useEffect(() => {
    setSelectedAccountId((current) => (
      accountsForClient.some((account) => account.id === current) ? current : accountsForClient[0]?.id || ""
    ));
  }, [accountsForClient]);

  const selectedAccount = useMemo(
    () => accountsForClient.find((account) => account.id === selectedAccountId) || null,
    [accountsForClient, selectedAccountId],
  );

  const scopeQuery = useMemo(() => getQuery({
    client_id: selectedClientId,
    ad_account_id: selectedAccountId,
    agency_id: role === "agency" ? agencyId || undefined : undefined,
  }), [agencyId, role, selectedAccountId, selectedClientId]);

  useEffect(() => {
    setReadiness(null);
    setTargets([]);
    setTargetKey("");
    setSubmittedCommand(null);
    setSelectedCommandId("");
    setReadinessLoading(false);
    if (!selectedClientId || !selectedAccountId) return;
    let cancelled = false;
    setReadinessLoading(true);
    void req<MetaBudgetReadiness>(`/provider-controls/meta/readiness${scopeQuery}`)
      .then((payload) => {
        if (!cancelled) setReadiness(normalizeReadiness(payload));
      })
      .catch(() => {
        // Capability discovery is fail-closed. Until the backend explicitly exposes
        // this feature, neither the panel nor its mutation controls are rendered.
        if (!cancelled) setReadiness(null);
      })
      .finally(() => {
        if (!cancelled) setReadinessLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [req, scopeQuery, selectedAccountId, selectedClientId]);

  const canLoadTargets = !!readiness?.visible
    && readiness.feature_enabled
    && readiness.binding_ready
    && readiness.credential_ready
    && (readiness.can_preview || readiness.can_read_history);

  useEffect(() => {
    setTargets([]);
    setTargetKey("");
    setTargetsError("");
    if (!canLoadTargets || !selectedAccountId) return;
    let cancelled = false;
    setTargetsLoading(true);
    const query = getQuery({
      client_id: selectedClientId,
      agency_id: role === "agency" ? agencyId || undefined : undefined,
    });
    void req<MetaBudgetTargetsResponse>(
      `/provider-controls/meta/accounts/${encodeURIComponent(selectedAccountId)}/budget-targets${query}`,
    ).then((payload) => {
      if (String(payload?.account_id || "") !== selectedAccountId) {
        throw new Error("Backend вернул бюджеты другого рекламного аккаунта. Изменения заблокированы.");
      }
      if (!cancelled) setTargets(normalizeTargets(payload));
    }).catch((error) => {
      if (!cancelled) setTargetsError(error instanceof Error ? error.message : "Не удалось прочитать бюджеты из Meta.");
    }).finally(() => {
      if (!cancelled) setTargetsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [agencyId, canLoadTargets, req, role, selectedAccountId, selectedClientId]);

  const loadHistory = useCallback(async () => {
    historyAbortRef.current?.abort();
    const controller = new AbortController();
    historyAbortRef.current = controller;
    const generation = ++historyRequestGeneration.current;
    const requestedClientId = selectedClientId;
    if (!requestedClientId) {
      setHistory([]);
      setHistoryClientId("");
      setHistoryLoading(false);
      if (historyAbortRef.current === controller) historyAbortRef.current = null;
      return;
    }
    setHistoryLoading(true);
    setHistoryError("");
    setHistoryErrorClientId(requestedClientId);
    try {
      const payload = await req<MetaBudgetHistoryResponse>(
        `/provider-controls/meta/budget-changes${getQuery({
          client_id: requestedClientId,
          agency_id: role === "agency" ? agencyId || undefined : undefined,
          limit: 25,
        })}`,
        { signal: controller.signal },
      );
      if (controller.signal.aborted
        || generation !== historyRequestGeneration.current
        || requestedClientId !== selectedClientRef.current) return;
      setHistory(normalizeHistory(payload));
      setHistoryClientId(requestedClientId);
    } catch (error) {
      if (controller.signal.aborted
        || generation !== historyRequestGeneration.current
        || requestedClientId !== selectedClientRef.current) return;
      // A refresh failure must not erase durable history already shown for
      // this client. Keep the last safe snapshot and surface the error.
      setHistoryError(error instanceof Error ? error.message : "Не удалось загрузить историю изменений.");
    } finally {
      if (generation === historyRequestGeneration.current) {
        setHistoryLoading(false);
        if (historyAbortRef.current === controller) historyAbortRef.current = null;
      }
    }
  }, [agencyId, req, role, selectedClientId]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const visibleHistory = useMemo(
    () => (historyClientId === selectedClientId ? history : []),
    [history, historyClientId, selectedClientId],
  );
  const visibleHistoryError = historyErrorClientId === selectedClientId ? historyError : "";

  const choices = useMemo<TargetChoice[]>(() => {
    const allowedTypes = new Set(readiness?.allowed?.target_types || []);
    const rows: TargetChoice[] = [];
    for (const target of targets) {
      if (!allowedTypes.has(target.target_type)) continue;
      const allowedFields = new Set(
        readiness?.allowed?.fields_by_target?.[target.target_type] || [],
      );
      for (const budget of target.budget_fields) {
        if (!allowedFields.has(budget.field)) continue;
        rows.push({
          key: `${target.target_type}:${target.provider_target_id}:${budget.field}`,
          target,
          budget,
        });
      }
    }
    return rows;
  }, [readiness?.allowed, targets]);

  useEffect(() => {
    setTargetKey((current) => (choices.some((choice) => choice.key === current) ? current : choices[0]?.key || ""));
  }, [choices]);

  const selectedChoice = useMemo(
    () => choices.find((choice) => choice.key === targetKey) || null,
    [choices, targetKey],
  );

  const invalidatePreview = useCallback(() => {
    previewAbortRef.current?.abort();
    previewAbortRef.current = null;
    previewRequestGeneration.current += 1;
    setPreview(null);
    setPreviewWarnings([]);
    setPreviewLoading(false);
    setPreviewError("");
    setConfirmed(false);
    setIdempotencyKey("");
    setSubmittedCommand(null);
    setSubmitError("");
  }, []);

  useEffect(() => {
    invalidatePreview();
    if (selectedChoice) {
      setAmountInput(minorToInput(selectedChoice.budget.current_minor, selectedChoice.budget.currency));
    } else {
      setAmountInput("");
    }
    setReason("");
  }, [invalidatePreview, selectedChoice]);

  useEffect(() => {
    if (!preview && !resolveRetryAt) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [preview, resolveRetryAt]);

  const parsedAmountMinor = selectedChoice
    ? majorInputToMinor(amountInput, selectedChoice.budget.currency)
    : null;
  const reasonNormalized = reason.trim().replace(/\s+/g, " ");
  const roleEligible = roleCanAttemptWrite(role, agencyMemberRole);
  const canMutate = !!readiness
    && readiness.visible
    && readiness.feature_enabled
    && readiness.credential_ready
    && readiness.binding_ready
    && readiness.can_preview
    && readiness.can_confirm
    && readiness.role === role
    && readiness.account?.id === selectedAccountId
    && roleEligible;
  const canMutateSelected = canMutate && selectedChoice?.budget.editable === true;
  const previewExpired = !!preview && new Date(preview.expires_at).getTime() <= clock;
  const previewNoop = preview?.delta_minor === 0;

  const buildRequest = useCallback((): MetaBudgetChangeRequest | null => {
    if (!selectedChoice || parsedAmountMinor == null || !reasonNormalized || reasonNormalized.length > 500) return null;
    return {
      client_id: selectedClientId,
      ad_account_id: selectedAccountId,
      ...(role === "agency" && agencyId ? { agency_id: agencyId } : {}),
      target_type: selectedChoice.target.target_type,
      provider_target_id: selectedChoice.target.provider_target_id,
      field: selectedChoice.budget.field,
      amount_minor: parsedAmountMinor,
      currency: selectedChoice.budget.currency,
      expected_current_minor: selectedChoice.budget.current_minor,
      reason: reasonNormalized,
    };
  }, [agencyId, parsedAmountMinor, reasonNormalized, role, selectedAccountId, selectedChoice, selectedClientId]);

  const currentDraft = buildRequest();
  draftFingerprintRef.current = currentDraft ? requestFingerprint(currentDraft) : "";

  async function createPreview() {
    const request = buildRequest();
    if (!canMutateSelected || !request || !selectedChoice) {
      setPreviewError("Выберите доступную цель, укажите новую сумму и обязательную причину изменения.");
      return;
    }
    previewAbortRef.current?.abort();
    const controller = new AbortController();
    previewAbortRef.current = controller;
    const generation = ++previewRequestGeneration.current;
    const fingerprint = requestFingerprint(request);
    setPreviewLoading(true);
    setPreviewError("");
    setSubmitError("");
    setConfirmed(false);
    try {
      const payload = await req<MetaBudgetPreview>("/provider-controls/meta/budget-changes/preview", {
        method: "POST",
        body: JSON.stringify(request),
        signal: controller.signal,
      });
      if (controller.signal.aborted
        || generation !== previewRequestGeneration.current
        || fingerprint !== draftFingerprintRef.current) return;
      const responseMatchesRequest = payload?.request
        && payload.request.client_id === request.client_id
        && payload.request.ad_account_id === request.ad_account_id
        && payload.request.target_type === request.target_type
        && payload.request.provider_target_id === request.provider_target_id
        && payload.request.field === request.field
        && payload.request.amount_minor === request.amount_minor
        && payload.request.currency === request.currency
        && payload.request.expected_current_minor === request.expected_current_minor
        && payload.request.reason === request.reason
        && (payload.request.agency_id || undefined) === (request.agency_id || undefined)
        && payload.requested_minor === request.amount_minor
        && payload.currency === request.currency
        && payload.delta_minor === payload.requested_minor - payload.current_minor;
      if (
        !responseMatchesRequest
        || !payload.preview_token
        || !Number.isSafeInteger(payload.current_minor)
        || !Number.isSafeInteger(payload.requested_minor)
        || !Number.isSafeInteger(payload.delta_minor)
      ) {
        throw new Error("Предпросмотр не прошёл проверку целостности. Ничего не было отправлено в Meta.");
      }
      setPreview(payload);
      setPreviewWarnings(calculatedWarnings(payload, selectedChoice.budget.current_minor));
      setIdempotencyKey(makeIdempotencyKey());
      setClock(Date.now());
    } catch (error) {
      if (controller.signal.aborted || generation !== previewRequestGeneration.current) return;
      setPreview(null);
      setPreviewWarnings([]);
      setIdempotencyKey("");
      setPreviewError(error instanceof Error ? error.message : "Не удалось подготовить безопасный предпросмотр.");
    } finally {
      if (generation === previewRequestGeneration.current) {
        setPreviewLoading(false);
        if (previewAbortRef.current === controller) previewAbortRef.current = null;
      }
    }
  }

  const selectedHistoryCommand = useMemo(
    () => visibleHistory.find((command) => command.id === selectedCommandId)
      || (submittedCommand?.request.client_id === selectedClientId ? submittedCommand : null),
    [selectedClientId, selectedCommandId, submittedCommand, visibleHistory],
  );
  const recoveryRoleEligible = role === "solo_client"
    || (role === "agency" && (agencyMemberRole === "owner" || agencyMemberRole === "manager"))
    || role === "admin";
  const recoveryAgencyScopeMatches = role === "agency"
    ? !!agencyId && selectedHistoryCommand?.request.agency_id === agencyId
    : role === "solo_client"
      ? !selectedHistoryCommand?.request.agency_id
      : true;
  const canRecoverSelectedCommand = !!selectedHistoryCommand
    && !!selectedAccount
    && !!readiness
    && readiness.role === role
    // This is a separate backend-owned recovery capability. It can remain
    // available when new writes are feature-disabled, and is the explicit
    // admin authorization rather than an assumption based on global role.
    && readiness.can_reconcile
    && readiness.binding_ready
    && readiness.credential_ready
    && readiness.account?.id === selectedAccountId
    && selectedHistoryCommand.request.ad_account_id === selectedAccountId
    && recoveryAgencyScopeMatches
    && recoveryRoleEligible;
  const selectedCommandWasReconciled = !!selectedHistoryCommand?.attempts?.some((attempt) => attempt.reconciliation === true);
  const resolveWaitSeconds = resolveRetryAt && selectedHistoryCommand && resolveRetryAt.commandId === selectedHistoryCommand.id
    ? Math.max(0, Math.ceil((resolveRetryAt.timestamp - clock) / 1_000))
    : 0;

  useEffect(() => {
    setResolveConfirmedCommandId("");
    setResolveRetryAt(null);
    setCommandActionError("");
  }, [selectedCommandId]);

  useEffect(() => {
    if (resolveRetryAt && resolveWaitSeconds === 0) setResolveRetryAt(null);
  }, [resolveRetryAt, resolveWaitSeconds]);

  const mergeCommand = useCallback((command: MetaBudgetCommand) => {
    if (command.request.client_id !== selectedClientRef.current) return;
    setHistoryClientId(command.request.client_id);
    setHistory((current) => [command, ...current.filter((item) => item.id !== command.id)]);
    setSubmittedCommand(command);
    setSelectedCommandId(command.id);
  }, []);

  const refreshCommand = useCallback(async (commandId: string) => {
    if (!commandId) return;
    setCheckingCommandId(commandId);
    setCommandActionError("");
    try {
      const payload = await req<MetaBudgetCommand | { command: MetaBudgetCommand }>(
        `/provider-controls/meta/budget-changes/${encodeURIComponent(commandId)}`,
      );
      const command = normalizedCommand(payload);
      if (!command || command.id !== commandId || command.request.client_id !== selectedClientRef.current) {
        throw new Error("Backend вернул статус другой команды. История не была изменена.");
      }
      mergeCommand(command);
    } catch (error) {
      setCommandActionError(error instanceof Error ? error.message : "Не удалось проверить статус команды.");
    } finally {
      setCheckingCommandId("");
    }
  }, [mergeCommand, req]);

  const reconcileUnknownCommand = useCallback(async (commandId: string) => {
    if (!commandId
      || selectedHistoryCommand?.id !== commandId
      || selectedHistoryCommand.status !== "unknown"
      || !canRecoverSelectedCommand) return;
    setCheckingCommandId(commandId);
    setCommandActionError("");
    setResolveConfirmedCommandId("");
    try {
      // Reconciliation is a read-only provider check. It must never retry the
      // original budget mutation when Meta's write outcome is unknown.
      const payload = await req<MetaBudgetCommand | { command: MetaBudgetCommand }>(
        `/provider-controls/meta/budget-changes/${encodeURIComponent(commandId)}/reconcile`,
        { method: "POST" },
      );
      const command = normalizedCommand(payload);
      if (!command || command.id !== commandId || command.request.client_id !== selectedClientRef.current) {
        throw new Error("Backend вернул результат сверки другой команды. История не была изменена.");
      }
      mergeCommand(command);
    } catch (error) {
      setCommandActionError(error instanceof Error ? error.message : "Не удалось сверить фактическое значение с Meta.");
    } finally {
      setCheckingCommandId("");
    }
  }, [canRecoverSelectedCommand, mergeCommand, req, selectedHistoryCommand]);

  const resolveUnknownCommand = useCallback(async (commandId: string) => {
    if (!commandId
      || resolveConfirmedCommandId !== commandId
      || selectedHistoryCommand?.id !== commandId
      || selectedHistoryCommand.status !== "unknown"
      || !selectedCommandWasReconciled
      || !canRecoverSelectedCommand
      || resolveWaitSeconds > 0) return;
    setResolvingCommandId(commandId);
    setCommandActionError("");
    try {
      // This endpoint only reads Meta and records the observed state as the
      // final outcome. It never retries the original budget write.
      const payload = await req<MetaBudgetCommand | { command: MetaBudgetCommand }>(
        `/provider-controls/meta/budget-changes/${encodeURIComponent(commandId)}/resolve-unknown`,
        {
          method: "POST",
          body: JSON.stringify({ confirm: true, resolution: "accept_current_state" }),
        },
      );
      const command = normalizedCommand(payload);
      if (!command || command.id !== commandId || command.request.client_id !== selectedClientRef.current) {
        throw new Error("Backend вернул результат другой команды. История не была изменена.");
      }
      mergeCommand(command);
      setResolveConfirmedCommandId("");
      setResolveRetryAt(null);
    } catch (error) {
      if (error instanceof ApiRequestError && error.code === "meta_budget_unknown_settlement_pending") {
        const seconds = Number(error.details?.retry_after_seconds);
        if (Number.isFinite(seconds) && seconds > 0) {
          setResolveRetryAt({ commandId, timestamp: Date.now() + Math.ceil(seconds) * 1_000 });
          setClock(Date.now());
        }
        setCommandActionError(
          Number.isFinite(seconds) && seconds > 0
            ? `Meta ещё может обновлять данные. Повторите не раньше чем через ${Math.ceil(seconds)} сек.`
            : "Meta ещё может обновлять данные. Подождите и повторите позже.",
        );
      } else {
        setCommandActionError(error instanceof Error ? error.message : "Не удалось принять текущее состояние Meta.");
      }
    } finally {
      setResolvingCommandId("");
    }
  }, [canRecoverSelectedCommand, mergeCommand, req, resolveConfirmedCommandId, resolveWaitSeconds, selectedCommandWasReconciled, selectedHistoryCommand]);

  async function submitChange() {
    if (!preview || !confirmed || !idempotencyKey || previewExpired || previewNoop || !canMutateSelected) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const payload = await req<MetaBudgetCommandResponse>("/provider-controls/meta/budget-changes", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ ...preview.request, preview_token: preview.preview_token, confirm: true }),
      });
      const command = normalizedCommand(payload);
      if (!command) throw new Error("Команда сохранена некорректно. Повторно её не отправляйте; обновите историю.");
      if (
        command.request.client_id !== preview.request.client_id
        || command.request.ad_account_id !== preview.request.ad_account_id
        || command.request.target_type !== preview.request.target_type
        || command.request.provider_target_id !== preview.request.provider_target_id
        || command.request.field !== preview.request.field
        || command.request.amount_minor !== preview.request.amount_minor
        || command.request.currency !== preview.request.currency
        || command.request.expected_current_minor !== preview.request.expected_current_minor
        || command.request.reason !== preview.request.reason
        || (command.request.agency_id || undefined) !== (preview.request.agency_id || undefined)
      ) {
        throw new Error("Backend вернул команду для другой цели. Повторно её не отправляйте; обновите историю.");
      }
      mergeCommand(command);
      await loadHistory();
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Не удалось отправить изменение в Meta.");
    } finally {
      setSubmitting(false);
    }
  }

  const historyVisible = readiness?.can_read_history === true || visibleHistory.length > 0 || !!visibleHistoryError;
  const providerVisible = readiness?.visible === true || visibleHistory.length > 0 || !!visibleHistoryError;
  if (!providerVisible && (readinessLoading || historyLoading)) return null;
  if (!providerVisible) return null;

  const notReady = !canMutate;
  const readinessTone = canMutate ? "good" : historyVisible ? "warn" : "bad";
  const permissionProblem = !!readiness && isMetaPermissionProblem(readiness);
  const reconnectProblem = !!readiness && requiresMetaReconnect(readiness);
  const selectedClientName = availableClients.find((client) => client.id === selectedClientId)?.name || "Клиент";
  const selectedStatusMeta = selectedHistoryCommand
    ? META_BUDGET_STATUS_META[selectedHistoryCommand.status]
    : null;

  return (
    <section className={`provider-budget-control ${compact ? "compact" : ""}`.trim()} aria-labelledby="meta-budget-heading">
      <article className="panel provider-budget-intro">
        <div>
          <div className="provider-budget-eyebrow">Реальные настройки рекламной платформы</div>
          <h2 id="meta-budget-heading">Бюджеты Meta</h2>
          <p>
            Плановые бюджеты выше — внутренний ориентир для отчётности. Здесь показаны фактические бюджеты кампаний
            и групп объявлений в Meta. Подтверждённое изменение будет отправлено в рекламный кабинет.
          </p>
        </div>
        <span className={`badge ${readinessTone}`} role="status">
          {canMutate
            ? "Изменения доступны"
            : readiness?.visible
              ? "Только просмотр"
              : visibleHistoryError
                ? "История недоступна"
                : "История доступна"}
        </span>
      </article>

      <article className="panel provider-budget-scope" aria-label="Область управления бюджетами Meta">
        <div className="provider-budget-scope-grid">
          <label>
            <span>1. Клиент</span>
            <select
              aria-label="Клиент для бюджета Meta"
              value={selectedClientId}
              onChange={(event) => setSelectedClientId(event.target.value)}
              disabled={availableClients.length <= 1 || previewLoading || submitting}
            >
              {availableClients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
            </select>
          </label>
          <label>
            <span>2. Рекламный аккаунт Meta</span>
            <select
              aria-label="Рекламный аккаунт Meta"
              value={selectedAccountId}
              onChange={(event) => setSelectedAccountId(event.target.value)}
              disabled={accountsForClient.length <= 1 || previewLoading || submitting}
            >
              <option value="">
                {accountsForClient.length ? "Выберите аккаунт Meta" : "Нет активных аккаунтов Meta"}
              </option>
              {accountsForClient.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}
            </select>
          </label>
        </div>
        {readiness ? (
          <div className={`provider-budget-readiness ${readinessTone}`} role="status">
            <strong>{canMutate ? "Подключение готово к безопасным изменениям" : reasonText(readiness)}</strong>
            <span>
              {canMutate
                ? "Перед каждой отправкой backend повторно читает значение из Meta и требует отдельное подтверждение."
                : "Обычное подключение или успешная синхронизация сами по себе не дают право менять бюджет."}
              {notReady && permissionProblem ? (
                <small className="provider-budget-support-code">Для поддержки: требуется разрешение ads_management.</small>
              ) : null}
            </span>
            {notReady && reconnectProblem ? (
              <a className="ghost-btn" href="/integrations?provider=meta">Проверить подключение Meta</a>
            ) : null}
          </div>
        ) : (
          <div className="provider-budget-readiness warn" role="status">
            <strong>Активный рекламный аккаунт Meta недоступен</strong>
            <span>
              История изменений сохранена и доступна ниже. Чтобы подготовить новое изменение, подключите или восстановите
              активный аккаунт Meta.
            </span>
          </div>
        )}
      </article>

      {canLoadTargets ? (
        <div className="provider-budget-layout">
          <article className="panel provider-budget-editor">
            <div className="provider-budget-section-head">
              <div>
                <div className="provider-budget-step">3. Цель и новое значение</div>
                <h3>Подготовить изменение</h3>
              </div>
              {selectedAccount ? <span className="muted-note">{selectedAccount.currency}</span> : null}
            </div>

            {targetsError ? <div className="warning" role="alert">{targetsError}</div> : null}
            {targetsLoading ? <div className="provider-budget-empty" role="status">Читаем актуальные бюджеты из Meta…</div> : null}
            {!targetsLoading && !targetsError && !choices.length ? (
              <div className="provider-budget-empty">
                В аккаунте нет доступных кампаний или групп объявлений с управляемым бюджетом.
              </div>
            ) : null}

            {choices.length ? (
              <>
                <label className="provider-budget-field">
                  <span>Кампания или группа объявлений</span>
                  <select
                    aria-label="Цель бюджета Meta"
                    value={targetKey}
                    onChange={(event) => setTargetKey(event.target.value)}
                    disabled={previewLoading || submitting}
                  >
                    {choices.map((choice) => (
                      <option key={choice.key} value={choice.key}>
                        {metaBudgetTargetLabel(choice.target.target_type)} · {choice.target.name} · {metaBudgetFieldLabel(choice.budget.field)}
                      </option>
                    ))}
                  </select>
                </label>

                {selectedChoice ? (
                  <div className="provider-budget-live" aria-live="polite">
                    <div>
                      <span>Сейчас в Meta</span>
                      <strong>{formatMinorMoney(selectedChoice.budget.current_minor, selectedChoice.budget.currency)}</strong>
                    </div>
                    <div>
                      <span>Тип</span>
                      <strong>{metaBudgetFieldLabel(selectedChoice.budget.field)}</strong>
                    </div>
                    <div>
                      <span>Проверено</span>
                      <strong>{formatDateTime(selectedChoice.budget.observed_at)}</strong>
                    </div>
                  </div>
                ) : null}

                {canMutateSelected ? (
                  <>
                    <div className="provider-budget-form-grid">
                      <label className="provider-budget-field">
                        <span>Новая сумма, {selectedChoice?.budget.currency || ""}</span>
                        <input
                          aria-label="Новая сумма бюджета Meta"
                          inputMode="decimal"
                          required
                          value={amountInput}
                          disabled={previewLoading || submitting}
                          onChange={(event) => {
                            setAmountInput(event.target.value);
                            invalidatePreview();
                          }}
                          placeholder="0"
                        />
                        <small>
                          Максимум {currencyFractionDigits(selectedChoice?.budget.currency || "USD")} знака(ов) после запятой.
                        </small>
                      </label>
                      <label className="provider-budget-field provider-budget-reason">
                        <span>Причина изменения <b aria-hidden="true">*</b></span>
                        <textarea
                          aria-label="Причина изменения бюджета Meta"
                          aria-required="true"
                          required
                          value={reason}
                          maxLength={500}
                          rows={3}
                          disabled={previewLoading || submitting}
                          onChange={(event) => {
                            setReason(event.target.value);
                            invalidatePreview();
                          }}
                          placeholder="Например: перераспределяем бюджет в кампанию с лучшей стоимостью лида"
                        />
                        <small>{reason.length}/500 · причина сохранится в истории</small>
                      </label>
                    </div>
                    {previewError ? <div className="warning" role="alert">{previewError}</div> : null}
                    <button
                      type="button"
                      className="primary-btn"
                      onClick={() => void createPreview()}
                      disabled={previewLoading || parsedAmountMinor == null || !reasonNormalized || reasonNormalized.length > 500}
                    >
                      {previewLoading ? "Сверяем с Meta…" : preview ? "Обновить предпросмотр" : "Проверить изменение"}
                    </button>
                  </>
                ) : (
                  <div className="provider-budget-readonly-note">
                    {selectedChoice?.budget.message
                      || (canMutate
                        ? "Этот бюджет нельзя изменить отдельно. Выберите другую кампанию или группу объявлений."
                        : "Ваша роль может видеть фактические значения и историю, но не может отправлять изменения в Meta.")}
                  </div>
                )}
              </>
            ) : null}
          </article>

          <aside className="panel provider-budget-preview" aria-label="Предпросмотр изменения бюджета Meta">
            <div className="provider-budget-step">4. Проверка и подтверждение</div>
            <h3>Что изменится в Meta</h3>
            {!preview ? (
              <div className="provider-budget-empty">
                Укажите сумму и причину, затем нажмите «Проверить изменение». До подтверждения ничего не отправляется в Meta.
              </div>
            ) : (
              <>
                <div className="provider-budget-preview-scope" aria-label="Проверяемая область изменения">
                  <strong>{selectedClientName} · {selectedAccount?.name || "Аккаунт Meta"}</strong>
                  <span>
                    {selectedChoice
                      ? `${metaBudgetTargetLabel(selectedChoice.target.target_type)} · ${selectedChoice.target.name} · ${metaBudgetFieldLabel(selectedChoice.budget.field)}`
                      : "Цель не выбрана"}
                  </span>
                </div>
                <div className="provider-budget-delta">
                  <div><span>Было</span><strong>{formatMinorMoney(preview.current_minor, preview.currency)}</strong></div>
                  <div aria-hidden="true" className="provider-budget-arrow">→</div>
                  <div><span>Станет</span><strong>{formatMinorMoney(preview.requested_minor, preview.currency)}</strong></div>
                </div>
                <div className={`provider-budget-delta-note ${preview.delta_minor < 0 ? "down" : "up"}`}>
                  {preview.delta_minor > 0 ? "+" : ""}{formatMinorMoney(preview.delta_minor, preview.currency)}
                </div>
                {previewWarnings.map((warning) => (
                  <div className={`provider-budget-warning ${warning.severity}`} key={warning.code} role="alert">
                    {warning.message}
                  </div>
                ))}
                <div className={`provider-budget-preview-expiry ${previewExpired ? "expired" : ""}`}>
                  {previewExpired
                    ? "Предпросмотр истёк. Получите новый — старый нельзя отправить."
                    : `Предпросмотр действителен до ${formatDateTime(preview.expires_at)}.`}
                </div>
                <label className="provider-budget-confirm">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                    disabled={previewExpired || previewNoop || submitting}
                  />
                  <span>
                    Я проверил(а) клиента, рекламный аккаунт, цель и сумму. Понимаю, что изменение будет отправлено в Meta.
                  </span>
                </label>
                {submitError ? <div className="warning" role="alert">{submitError}</div> : null}
                <button
                  type="button"
                  className="primary-btn provider-budget-submit"
                  onClick={() => void submitChange()}
                  disabled={!confirmed || previewExpired || previewNoop || submitting || !!submittedCommand}
                >
                  {submitting
                    ? "Отправляем один раз…"
                    : submittedCommand
                      ? "Команда уже отправлена"
                      : "Подтвердить и отправить в Meta"}
                </button>
              </>
            )}
          </aside>
        </div>
      ) : null}

      {historyVisible ? (
        <article className="panel provider-budget-history">
          <div className="provider-budget-section-head">
            <div>
              <div className="provider-budget-step">История и контроль результата</div>
              <h3>Изменения бюджетов Meta</h3>
            </div>
            <button type="button" className="ghost-btn" onClick={() => void loadHistory()} disabled={historyLoading}>
              {historyLoading ? "Обновляем…" : "Обновить"}
            </button>
          </div>
          {visibleHistoryError ? <div className="warning" role="alert">{visibleHistoryError}</div> : null}
          <div className="budgets-table-wrap" role="region" aria-label="Таблица изменений бюджетов Meta" tabIndex={0}>
            <table className="provider-budget-history-table">
              <thead>
                <tr>
                  <th>Когда</th>
                  <th>Цель</th>
                  <th>Изменение</th>
                  <th>Причина</th>
                  <th>Статус</th>
                  <th aria-label="Действия" />
                </tr>
              </thead>
              <tbody>
                {visibleHistory.map((command) => {
                  const meta = META_BUDGET_STATUS_META[command.status];
                  return (
                    <tr key={command.id} className={command.id === selectedCommandId ? "selected" : ""}>
                      <td>{formatDateTime(command.created_at)}</td>
                      <td>{metaBudgetTargetLabel(command.request.target_type)} · {metaBudgetFieldLabel(command.request.field)}</td>
                      <td>
                        {formatMinorMoney(command.observed_before_minor, command.request.currency)} →{" "}
                        {formatMinorMoney(command.request.amount_minor, command.request.currency)}
                      </td>
                      <td className="provider-budget-history-reason">{command.request.reason}</td>
                      <td><span className={`badge ${meta.tone}`}>{meta.label}</span></td>
                      <td>
                        <button
                          type="button"
                          className="mini-btn"
                          aria-label={`Детали изменения от ${formatDateTime(command.created_at)}: ${metaBudgetTargetLabel(command.request.target_type)}, ${meta.label}`}
                          onClick={() => setSelectedCommandId(command.id)}
                        >
                          Детали
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!historyLoading && !visibleHistory.length ? (
                  <tr><td colSpan={6} className="muted-note">Подтверждённых попыток изменения пока нет.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
          {selectedHistoryCommand && selectedStatusMeta ? (
            <div className={`provider-budget-command-detail ${selectedHistoryCommand.status}`} aria-live="polite">
              <div>
                <strong>{selectedStatusMeta.label}</strong>
                <span>{selectedStatusMeta.help}</span>
                {selectedHistoryCommand.error?.message ? <span>Причина: {selectedHistoryCommand.error.message}</span> : null}
                {selectedHistoryCommand.status === "unknown" && !canRecoverSelectedCommand ? (
                  <span>
                    Сверка с Meta доступна только владельцу или менеджеру с готовым подключением исходного аккаунта.
                  </span>
                ) : null}
              </div>
              {(selectedHistoryCommand.status === "queued" || selectedHistoryCommand.status === "in_progress") ? (
                <button
                  type="button"
                  className="ghost-btn"
                  disabled={checkingCommandId === selectedHistoryCommand.id}
                  onClick={() => void refreshCommand(selectedHistoryCommand.id)}
                >
                  {checkingCommandId === selectedHistoryCommand.id ? "Проверяем…" : "Обновить статус"}
                </button>
              ) : null}
              {selectedHistoryCommand.status === "unknown"
                && canRecoverSelectedCommand
                && !selectedCommandWasReconciled ? (
                  <button
                    type="button"
                    className="ghost-btn"
                    disabled={checkingCommandId === selectedHistoryCommand.id}
                    onClick={() => void reconcileUnknownCommand(selectedHistoryCommand.id)}
                  >
                    {checkingCommandId === selectedHistoryCommand.id ? "Проверяем…" : "Проверить статус"}
                  </button>
                ) : null}
            </div>
          ) : null}
          {selectedHistoryCommand?.status === "unknown"
            && canRecoverSelectedCommand
            && selectedCommandWasReconciled ? (
              <div className="provider-budget-resolve-unknown" role="group" aria-labelledby="resolve-unknown-title">
                <div>
                  <strong id="resolve-unknown-title">Сверка не смогла подтвердить прежнюю команду</strong>
                  <span>
                    Новая запись в Meta не отправится. Система ещё раз прочитает текущее значение, зафиксирует его как итог
                    и снимет блокировку с этой цели.
                  </span>
                </div>
                <label className="provider-budget-confirm">
                  <input
                    type="checkbox"
                    checked={resolveConfirmedCommandId === selectedHistoryCommand.id}
                    onChange={(event) => setResolveConfirmedCommandId(event.target.checked ? selectedHistoryCommand.id : "")}
                    disabled={resolvingCommandId === selectedHistoryCommand.id}
                  />
                  <span>Я проверил(а) текущее состояние в Meta и согласен(на) принять его как итог этой команды.</span>
                </label>
                <button
                  type="button"
                  className="ghost-btn"
                  disabled={resolveConfirmedCommandId !== selectedHistoryCommand.id
                    || resolvingCommandId === selectedHistoryCommand.id
                    || resolveWaitSeconds > 0}
                  onClick={() => void resolveUnknownCommand(selectedHistoryCommand.id)}
                >
                  {resolvingCommandId === selectedHistoryCommand.id
                    ? "Сверяем текущее состояние…"
                    : resolveWaitSeconds > 0
                      ? `Повторить через ${resolveWaitSeconds} сек.`
                      : "Принять текущее состояние Meta"}
                </button>
              </div>
            ) : null}
          {commandActionError ? <div className="warning" role="alert">{commandActionError}</div> : null}
        </article>
      ) : null}
    </section>
  );
}
