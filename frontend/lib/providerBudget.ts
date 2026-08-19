export type MetaBudgetTargetType = "campaign" | "ad_set";
export type MetaBudgetField = "daily_budget" | "lifetime_budget";
export type MetaBudgetCommandStatus =
  | "previewed"
  | "queued"
  | "in_progress"
  | "applied"
  | "conflict"
  | "failed"
  | "unknown";

export type MetaBudgetReadiness = {
  provider: "meta";
  feature_enabled: boolean;
  visible: boolean;
  can_read_history: boolean;
  can_preview: boolean;
  can_confirm: boolean;
  can_reconcile: boolean;
  credential_ready: boolean;
  binding_ready: boolean;
  reason_code?: string | null;
  message?: string | null;
  role?: string | null;
  account?: {
    id: string;
    name: string;
    currency: string;
  } | null;
  allowed?: {
    target_types?: Array<MetaBudgetTargetType | "account">;
    fields_by_target?: Partial<Record<MetaBudgetTargetType | "account", Array<MetaBudgetField | "spend_cap">>>;
  } | null;
};

export type MetaBudgetTargetField = {
  field: MetaBudgetField;
  current_minor: number;
  currency: string;
  observed_at: string;
  editable: boolean;
  reason_code?: string | null;
  message?: string | null;
};

export type MetaBudgetTarget = {
  target_type: MetaBudgetTargetType;
  provider_target_id: string;
  name: string;
  status?: string | null;
  budget_fields: MetaBudgetTargetField[];
};

export type MetaBudgetTargetsResponse = {
  provider?: "meta";
  account_id: string;
  items: MetaBudgetTarget[];
  count?: number;
  observed_at?: string | null;
};

export type MetaBudgetWarning = {
  code: string;
  message: string;
  severity: "info" | "warning" | "critical";
};

export type MetaBudgetChangeRequest = {
  client_id: string;
  ad_account_id: string;
  agency_id?: string;
  target_type: MetaBudgetTargetType;
  provider_target_id: string;
  field: MetaBudgetField;
  amount_minor: number;
  currency: string;
  expected_current_minor: number;
  reason: string;
};

export type MetaBudgetPreview = {
  preview_token: string;
  issued_at: string;
  expires_at: string;
  current_minor: number;
  requested_minor: number;
  delta_minor: number;
  currency: string;
  warnings?: MetaBudgetWarning[];
  request: MetaBudgetChangeRequest;
};

export type MetaBudgetCommandError = {
  code: string;
  message: string;
  retryable?: boolean;
};

export type MetaBudgetCommandAttempt = {
  attempt_no: number;
  outcome: MetaBudgetCommandStatus;
  started_at: string;
  finished_at: string;
  observed_before_minor?: number | null;
  confirmed_after_minor?: number | null;
  error?: MetaBudgetCommandError | null;
  reconciliation?: boolean;
};

export type MetaBudgetCommand = {
  id: string;
  status: MetaBudgetCommandStatus;
  request: MetaBudgetChangeRequest;
  observed_before_minor: number;
  confirmed_after_minor?: number | null;
  attempt_count: number;
  error?: MetaBudgetCommandError | null;
  attempts?: MetaBudgetCommandAttempt[];
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
};

export type MetaBudgetCommandResponse = {
  command: MetaBudgetCommand;
  replayed: boolean;
};

export type MetaBudgetHistoryResponse = {
  items: MetaBudgetCommand[];
  count?: number;
};

const ZERO_DECIMAL_CURRENCIES = new Set([
  "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
]);
const THREE_DECIMAL_CURRENCIES = new Set(["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"]);

export function currencyFractionDigits(currency: string): number {
  const normalized = String(currency || "").trim().toUpperCase();
  if (ZERO_DECIMAL_CURRENCIES.has(normalized)) return 0;
  if (THREE_DECIMAL_CURRENCIES.has(normalized)) return 3;
  return 2;
}

export function formatMinorMoney(amountMinor: number | null | undefined, currency: string): string {
  const normalizedCurrency = String(currency || "USD").trim().toUpperCase();
  const digits = currencyFractionDigits(normalizedCurrency);
  const amount = Number.isSafeInteger(amountMinor) ? Number(amountMinor) / (10 ** digits) : 0;
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency: normalizedCurrency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(amount);
  } catch {
    return `${amount.toFixed(digits)} ${normalizedCurrency}`;
  }
}

export function minorToInput(amountMinor: number, currency: string): string {
  const digits = currencyFractionDigits(currency);
  if (!Number.isSafeInteger(amountMinor) || amountMinor < 0) return "";
  if (digits === 0) return String(amountMinor);
  const divider = 10 ** digits;
  const whole = Math.floor(amountMinor / divider);
  const fraction = String(amountMinor % divider).padStart(digits, "0");
  return `${whole}.${fraction}`;
}

export function majorInputToMinor(value: string, currency: string): number | null {
  const digits = currencyFractionDigits(currency);
  const normalized = String(value || "").trim().replace(",", ".");
  const match = normalized.match(/^(0|[1-9][0-9]*)(?:\.([0-9]+))?$/);
  if (!match) return null;
  const fraction = match[2] || "";
  if (fraction.length > digits) return null;
  const scale = 10n ** BigInt(digits);
  const minor = BigInt(match[1]) * scale + BigInt((fraction + "0".repeat(digits)).slice(0, digits) || "0");
  if (minor <= 0n || minor > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(minor);
}

export function makeIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `meta-budget-${crypto.randomUUID()}`;
  }
  return `meta-budget-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function metaBudgetTargetLabel(value: MetaBudgetTargetType): string {
  return value === "ad_set" ? "Группа объявлений" : "Кампания";
}

export function metaBudgetFieldLabel(value: MetaBudgetField): string {
  return value === "lifetime_budget" ? "Бюджет на весь срок" : "Дневной бюджет";
}

export const META_BUDGET_STATUS_META: Record<MetaBudgetCommandStatus, { label: string; tone: string; help: string }> = {
  previewed: { label: "Предпросмотр", tone: "", help: "Изменение ещё не отправлено в Meta." },
  queued: { label: "В очереди", tone: "warn", help: "Команда сохранена и ожидает выполнения." },
  in_progress: { label: "Выполняется", tone: "warn", help: "Meta обрабатывает изменение." },
  applied: { label: "Применено", tone: "good", help: "Новое значение подтверждено повторным чтением из Meta." },
  conflict: { label: "Конфликт", tone: "bad", help: "Значение в Meta изменилось после предпросмотра. Создайте новый предпросмотр." },
  failed: { label: "Не применено", tone: "bad", help: "Meta отклонила изменение; сохранённое значение не подтверждено." },
  unknown: { label: "Статус неизвестен", tone: "bad", help: "Не повторяйте команду: сначала проверьте фактическое значение в Meta." },
};
