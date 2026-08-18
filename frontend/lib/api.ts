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
