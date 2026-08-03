export type AppRole = "admin" | "agency" | "client";

const SAFE_URL_ORIGIN = "https://client-dash-up.local";
const PUBLIC_PATHS = new Set(["/login", "/login/success"]);
const WORKSPACE_PREFIXES = [
  "/clients",
  "/accounts",
  "/budgets",
  "/client",
  "/agency",
  "/traffic",
  "/integrations",
  "/sync-monitor",
] as const;

function matchesRoute(pathname: string, route: string): boolean {
  return pathname === route || pathname.startsWith(`${route}/`);
}

export function isAppRole(value: unknown): value is AppRole {
  return value === "admin" || value === "agency" || value === "client";
}

function pathnameFromRelativePath(path: string): string {
  try {
    return new URL(path, SAFE_URL_ORIGIN).pathname;
  } catch {
    return "/";
  }
}

export function safeRelativePath(value: string | null | undefined, fallback = "/"): string {
  const candidate = String(value || "").trim();
  if (
    !candidate.startsWith("/") ||
    candidate.startsWith("//") ||
    candidate.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(candidate)
  ) {
    return fallback;
  }

  try {
    const parsed = new URL(candidate, SAFE_URL_ORIGIN);
    if (parsed.origin !== SAFE_URL_ORIGIN) return fallback;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname);
}

export function homeForRole(role: AppRole): string {
  if (role === "admin") return "/platform";
  if (role === "client") return "/portal";
  return "/";
}

export function isPathAllowedForRole(role: AppRole, pathname: string): boolean {
  if (isPublicPath(pathname)) return false;

  if (matchesRoute(pathname, "/platform")) {
    return role === "admin";
  }

  if (matchesRoute(pathname, "/portal")) {
    return role === "client";
  }

  if (pathname === "/" || WORKSPACE_PREFIXES.some((route) => matchesRoute(pathname, route))) {
    return role === "admin" || role === "agency";
  }

  return false;
}

export function destinationForRole(role: AppRole, requestedPath?: string | null): string {
  const safePath = safeRelativePath(requestedPath, homeForRole(role));
  const pathname = pathnameFromRelativePath(safePath);
  return isPathAllowedForRole(role, pathname) ? safePath : homeForRole(role);
}
