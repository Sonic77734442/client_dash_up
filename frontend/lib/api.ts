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

export async function fetchJson<T>(
  baseUrl: string,
  path: string,
  token?: string,
  init?: RequestInit
): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const headers = new Headers(init?.headers || {});
  if (!headers.has("Content-Type") && method !== "GET") {
    headers.set("Content-Type", "application/json");
  }
  if (["POST", "PATCH", "PUT", "DELETE"].includes(method) && !headers.has(CSRF_HEADER_NAME)) {
    const csrf = readCookie(CSRF_COOKIE_NAME);
    if (csrf) headers.set(CSRF_HEADER_NAME, csrf);
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

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
