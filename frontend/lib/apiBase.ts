"use client";

const LS_API_BASE = "ops_api_base";
export const DEFAULT_API_BASE = "/api/backend";

function trimTrailingSlash(v: string): string {
  return v.replace(/\/+$/, "");
}

export function normalizeApiBase(input: string, fallback: string): string {
  if (process.env.NODE_ENV === "production") {
    return DEFAULT_API_BASE;
  }
  const candidate = (input || "").trim();
  const backup = (fallback || "").trim();
  function normalize(value: string): string | null {
    if (!value) return null;
    if (value.startsWith("/")) {
      return `/${value.replace(/^\/+|\/+$/g, "")}`;
    }
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return null;
      }
      return trimTrailingSlash(parsed.toString());
    } catch {
      return null;
    }
  }

  const normalized = normalize(candidate) || normalize(backup);
  if (normalized) return normalized;
  return DEFAULT_API_BASE;
}

export function resolveApiBase(defaultApiBase: string): string {
  if (typeof window === "undefined") {
    return normalizeApiBase(defaultApiBase, DEFAULT_API_BASE);
  }
  const normalizedDefault = normalizeApiBase(defaultApiBase, DEFAULT_API_BASE);
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  if (!tokenLoginEnabled || normalizedDefault.startsWith("/")) {
    localStorage.removeItem(LS_API_BASE);
    return normalizedDefault;
  }
  const stored = localStorage.getItem(LS_API_BASE) || "";
  const next = normalizeApiBase(stored, normalizedDefault);
  if (stored !== next) {
    localStorage.setItem(LS_API_BASE, next);
  }
  return next;
}
