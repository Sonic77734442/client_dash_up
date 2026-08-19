import { getSessionToken } from "./sessionToken";
import { agencySelectionRequiredMessage } from "./agencyContext";

export type ApiErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export class ApiRequestError extends Error {
  status: number;
  code: string;
  details?: Record<string, unknown>;

  constructor(message: string, status: number, code = "", details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const API_ERROR_MESSAGES_RU: Record<string, string> = {
  agency_access_denied: "У вас нет активного доступа к выбранному агентству.",
  agency_manage_forbidden:
    "Подключать источники и импортировать рекламные аккаунты может только владелец или менеджер агентства.",
  agency_membership_check_failed:
    "Не удалось безопасно проверить доступ к агентству. Обновите страницу и попробуйте снова.",
  agency_unbound:
    "Пользователь ещё не добавлен ни в одно активное агентство. Администратор должен назначить его участником.",
  agency_client_access_denied:
    "Этот клиент ещё не привязан к выбранному агентству.",
  client_invites_disabled:
    "Администратор платформы отключил приглашения клиентов для этого агентства.",
  last_active_agency_owner:
    "Сначала назначьте другого активного владельца агентства, затем измените роль или удалите текущего.",
  provider_connect_forbidden:
    "Ваша роль не позволяет подключать рекламные платформы.",
  session_required: "Сначала войдите в платформу, затем повторите подключение.",
  meta_budget_controls_disabled: "Управление бюджетами Meta пока выключено.",
  meta_budget_client_not_allowed: "Управление бюджетами Meta ещё не включено для этого клиента.",
  meta_budget_role_forbidden: "Ваша роль не позволяет управлять бюджетами Meta.",
  meta_budget_write_scope_not_ready: "Текущая роль или подключение не разрешает менять бюджеты Meta.",
  meta_budget_scope_inactive: "Клиент или рекламный аккаунт Meta неактивен.",
  meta_budget_provider_mismatch: "Выбранный рекламный аккаунт не относится к Meta.",
  meta_budget_account_invalid: "Не удалось подтвердить идентификатор рекламного аккаунта Meta.",
  meta_budget_account_scope_mismatch: "Рекламный аккаунт не относится к выбранному клиенту.",
  meta_budget_assignment_conflict: "У рекламного аккаунта конфликт назначения клиенту.",
  meta_budget_rediscovery_required: "Заново найдите и импортируйте этот аккаунт через подключение Meta.",
  meta_budget_credential_storage_not_ready: "Подключение Meta должен безопасно обновить администратор платформы.",
  meta_budget_credential_not_ready: "Подключение Meta не готово. Переподключите платформу.",
  meta_budget_credential_incomplete: "В подключении Meta не хватает обязательных данных.",
  meta_permissions_missing: "Переподключите Meta и разрешите управление рекламными бюджетами.",
  meta_budget_ads_management_missing: "Переподключите Meta и разрешите управление рекламными бюджетами.",
  meta_budget_currency_mismatch: "Валюта изменения не совпадает с валютой рекламного аккаунта.",
  meta_budget_target_not_editable: "Этот бюджет нельзя изменить отдельно в Meta.",
  meta_budget_target_quarantined: "Предыдущая команда имеет неизвестный результат. Сначала выполните сверку с Meta.",
  meta_budget_target_busy: "Для этой цели уже выполняется другая команда. Обновите статус и повторите позже.",
  meta_budget_target_lock_lost: "Безопасная блокировка команды истекла. Обновите историю перед следующим действием.",
  meta_budget_preview_secret_missing: "Безопасный предпросмотр не настроен. Обратитесь к администратору.",
  meta_budget_preview_ttl_invalid: "Срок действия предпросмотра настроен неверно. Обратитесь к администратору.",
  meta_budget_provider_configuration_missing: "Интеграция управления бюджетами Meta настроена не полностью.",
  meta_budget_rollout_configuration_invalid: "Настройки доступа к управлению бюджетами некорректны.",
  preview_expired: "Предпросмотр истёк. Получите новый перед подтверждением.",
  preview_tampered: "Предпросмотр не прошёл проверку целостности. Ничего не отправлено в Meta.",
  preview_request_mismatch: "Параметры изменились после предпросмотра. Получите новый предпросмотр.",
  preview_already_consumed: "Этот предпросмотр уже использован. Обновите историю команды.",
  idempotency_key_invalid: "Не удалось создать безопасный идентификатор команды. Обновите предпросмотр.",
  idempotency_conflict: "Эта команда уже использовалась с другими параметрами. Обновите предпросмотр.",
  command_not_found: "Команда изменения бюджета не найдена.",
  command_not_reconcilable: "Эту команду уже нельзя сверить как неизвестную.",
  meta_budget_unknown_settlement_pending: "Meta ещё может обновлять данные. Подождите перед фиксацией текущего состояния.",
  client_read_only: "Клиентский доступ позволяет только просматривать данные.",
};

function localizedApiErrorMessage(code: string, fallback: string): string {
  return API_ERROR_MESSAGES_RU[code] || fallback;
}

function normalizeQueryPath(path: string): string {
  const qIndex = path.indexOf("?");
  if (qIndex < 0) return path;
  const base = path.slice(0, qIndex + 1);
  let query = path.slice(qIndex + 1);
  if (!query) return path;

  // Defensive fix for malformed concatenated query strings like
  // client_id=...status=active...date_from=...
  const knownKeys = [
    "client_id",
    "account_id",
    "status",
    "date_from",
    "date_to",
    "provider",
    "platform",
    "scope",
    "limit",
    "offset",
    "search",
  ];
  for (const key of knownKeys) {
    const needle = `${key}=`;
    let cursor = query.indexOf(needle, 1);
    while (cursor > -1) {
      if (query[cursor - 1] !== "&") {
        query = `${query.slice(0, cursor)}&${query.slice(cursor)}`;
        cursor += 1;
      }
      cursor = query.indexOf(needle, cursor + needle.length);
    }
  }
  return `${base}${query}`;
}

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const parts = document.cookie ? document.cookie.split(";") : [];
  const prefix = `${name}=`;
  for (const part of parts) {
    const v = part.trim();
    if (v.startsWith(prefix)) return decodeURIComponent(v.slice(prefix.length));
  }
  return "";
}

const CSRF_COOKIE_NAME = process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME || "ops_csrf";
const CSRF_HEADER_NAME = process.env.NEXT_PUBLIC_CSRF_HEADER_NAME || "X-CSRF-Token";

let csrfMemoryToken = "";

function readStoredCsrfToken(): string {
  return csrfMemoryToken;
}

function storeCsrfToken(token: string): void {
  const value = (token || "").trim();
  if (!value) return;
  csrfMemoryToken = value;
}

function clearStoredCsrfToken(): void {
  csrfMemoryToken = "";
}

async function resolveCsrfToken(baseUrl: string): Promise<string> {
  const fromCookie = readCookie(CSRF_COOKIE_NAME);
  if (fromCookie) {
    storeCsrfToken(fromCookie);
    return fromCookie;
  }
  const fromStorage = readStoredCsrfToken();
  if (fromStorage) return fromStorage;

  // Recover a fresh token when the readable same-origin CSRF cookie is not available yet.
  const res = await fetch(`${baseUrl}/auth/csrf`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) return "";
  let body: unknown = {};
  try {
    body = await res.json();
  } catch {
    return "";
  }
  const token = String((body as { csrf_token?: unknown })?.csrf_token || "").trim();
  if (token) storeCsrfToken(token);
  return token;
}

export async function fetchJson<T>(
  baseUrl: string,
  path: string,
  token?: string,
  init?: RequestInit
): Promise<T> {
  const normalizedPath = normalizeQueryPath(path);
  const method = (init?.method || "GET").toUpperCase();
  const resolvedToken = (token || "").trim();
  const fallbackToken =
    typeof window !== "undefined"
      ? getSessionToken()
      : "";

  async function requestOnce(forceRefreshCsrf: boolean): Promise<{ res: Response; body: unknown }> {
    const headers = new Headers(init?.headers || {});
    if (!headers.has("Content-Type") && method !== "GET") {
      headers.set("Content-Type", "application/json");
    }
    if (["POST", "PATCH", "PUT", "DELETE"].includes(method) && !headers.has(CSRF_HEADER_NAME)) {
      if (forceRefreshCsrf) clearStoredCsrfToken();
      const csrf = await resolveCsrfToken(baseUrl);
      if (csrf) headers.set(CSRF_HEADER_NAME, csrf);
    }
    if (resolvedToken) {
      headers.set("Authorization", `Bearer ${resolvedToken}`);
    } else if (forceRefreshCsrf && fallbackToken) {
      // Fallback only after an auth failure retry path (cross-site cookie blocked scenarios).
      headers.set("Authorization", `Bearer ${fallbackToken}`);
    }

    const res = await fetch(`${baseUrl}${normalizedPath}`, {
      ...init,
      headers,
      credentials: "include",
    });

    let body: unknown = {};
    try {
      body = await res.json();
    } catch {
      body = {};
    }
    return { res, body };
  }

  let { res, body } = await requestOnce(false);
  if (!res.ok && res.status === 403) {
    const envelope = body as ApiErrorEnvelope;
    if ((envelope?.error?.code || "").trim() === "csrf_failed") {
      ({ res, body } = await requestOnce(true));
    }
  }
  if (!res.ok && res.status === 401 && !resolvedToken && fallbackToken) {
    ({ res, body } = await requestOnce(true));
  }

  if (!res.ok) {
    const envelope = body as ApiErrorEnvelope;
    const code = String(envelope?.error?.code || "").trim();
    const fallbackMessage = envelope?.error?.message || `Запрос завершился ошибкой (${res.status})`;
    const msg = code === "selection_required"
      ? agencySelectionRequiredMessage()
      : localizedApiErrorMessage(code, fallbackMessage);
    throw new ApiRequestError(msg, res.status, code, envelope?.error?.details);
  }

  return body as T;
}

export function getQuery(params: Record<string, string | number | undefined | null>) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}
