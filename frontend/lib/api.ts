export type ApiErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

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
const CSRF_STORAGE_KEY = "ops_csrf_token";

let csrfMemoryToken = "";

function readStoredCsrfToken(): string {
  if (typeof window === "undefined") return "";
  if (csrfMemoryToken) return csrfMemoryToken;
  const stored = (localStorage.getItem(CSRF_STORAGE_KEY) || "").trim();
  if (stored) csrfMemoryToken = stored;
  return stored;
}

function storeCsrfToken(token: string): void {
  const value = (token || "").trim();
  if (!value || typeof window === "undefined") return;
  csrfMemoryToken = value;
  localStorage.setItem(CSRF_STORAGE_KEY, value);
}

function clearStoredCsrfToken(): void {
  csrfMemoryToken = "";
  if (typeof window === "undefined") return;
  localStorage.removeItem(CSRF_STORAGE_KEY);
}

async function resolveCsrfToken(baseUrl: string): Promise<string> {
  const fromCookie = readCookie(CSRF_COOKIE_NAME);
  if (fromCookie) {
    storeCsrfToken(fromCookie);
    return fromCookie;
  }
  const fromStorage = readStoredCsrfToken();
  if (fromStorage) return fromStorage;

  // Cross-domain SPA cannot read API-domain cookies; ask API for CSRF token explicitly.
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
  const method = (init?.method || "GET").toUpperCase();
  const resolvedToken = (token || "").trim();

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
    if (resolvedToken) headers.set("Authorization", `Bearer ${resolvedToken}`);

    const res = await fetch(`${baseUrl}${path}`, {
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

  if (!res.ok) {
    const envelope = body as ApiErrorEnvelope;
    const msg = envelope?.error?.message || `Request failed (${res.status})`;
    throw new Error(msg);
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
