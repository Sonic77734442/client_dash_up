"use client";

const LS_API_BASE = "ops_api_base";

function trimTrailingSlash(v: string): string {
  return v.replace(/\/+$/, "");
}

export function normalizeApiBase(input: string, fallback: string): string {
  const candidate = (input || "").trim();
  const backup = (fallback || "").trim();
  const base = candidate || backup;
  try {
    const parsed = new URL(base);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new Error("invalid protocol");
    }
    return trimTrailingSlash(parsed.toString());
  } catch {
    const parsedFallback = new URL(backup || "http://localhost:8000");
    return trimTrailingSlash(parsedFallback.toString());
  }
}

export function resolveApiBase(defaultApiBase: string): string {
  if (typeof window === "undefined") {
    return normalizeApiBase(defaultApiBase, "http://localhost:8000");
  }
  const stored = localStorage.getItem(LS_API_BASE) || "";
  const next = normalizeApiBase(stored, defaultApiBase);
  if (stored !== next) {
    localStorage.setItem(LS_API_BASE, next);
  }
  return next;
}
