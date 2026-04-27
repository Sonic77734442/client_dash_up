"use client";

import Link from "next/link";
import { useSessionContext } from "../hooks/useSessionContext";
import { useLocale } from "../hooks/useLocale";
import { t } from "../lib/i18n";
import { resolveApiBase } from "../lib/apiBase";

type SidebarSection =
  | "dashboard"
  | "accounts"
  | "integrations"
  | "traffic"
  | "sync_monitor"
  | "budgets"
  | "clients"
  | "platform_admin";

function itemClass(section: SidebarSection, active: SidebarSection) {
  return `menu-item ${section === active ? "active" : ""}`.trim();
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

export function AppSidebar({
  active,
  subtitle = "Digital Operations",
  className = "sidebar",
}: {
  active: SidebarSection;
  subtitle?: string;
  className?: string;
}) {
  const { context } = useSessionContext();
  const { locale } = useLocale();
  const showPlatformAdmin = Boolean(context?.valid) && context?.role === "admin";
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";

  async function handleLogout() {
    try {
      const apiBase = resolveApiBase(defaultApiBase);
      const token = tokenLoginEnabled ? (localStorage.getItem("ops_session_token") || "") : "";
      const headers: HeadersInit = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const csrfHeaderName = process.env.NEXT_PUBLIC_CSRF_HEADER_NAME || "X-CSRF-Token";
      const csrfCookieName = process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME || "ops_csrf";
      const csrfToken = readCookie(csrfCookieName);
      if (csrfToken) headers[csrfHeaderName] = csrfToken;
      await fetch(`${apiBase}/auth/logout`, {
        method: "POST",
        headers,
        credentials: "include",
      });
    } catch {
      // noop
    } finally {
      localStorage.removeItem("ops_session_token");
      localStorage.removeItem("ops_api_base");
      window.dispatchEvent(new Event("ops-session-updated"));
      window.location.replace("/login");
    }
  }

  return (
    <aside className={className}>
      <div className="brand">Editorial Rigor</div>
      <div className="panel-subtitle">{subtitle}</div>
      <nav className="menu">
        <Link className={itemClass("dashboard", active)} href="/">{t(locale, "tab_dashboard", "Dashboard")}</Link>
        <Link className={itemClass("accounts", active)} href="/accounts">{t(locale, "tab_accounts", "Accounts")}</Link>
        <Link className={itemClass("traffic", active)} href="/traffic">{t(locale, "tab_traffic", "Traffic")}</Link>
        <Link className={itemClass("integrations", active)} href="/integrations">{t(locale, "tab_integrations", "Integrations")}</Link>
        <Link className={itemClass("sync_monitor", active)} href="/sync-monitor">{t(locale, "tab_sync_monitor", "Sync Monitor")}</Link>
        <Link className={itemClass("budgets", active)} href="/budgets">{t(locale, "tab_budgets", "Budgets")}</Link>
        <Link className={itemClass("clients", active)} href="/clients">{t(locale, "tab_clients", "Clients")}</Link>
        {showPlatformAdmin ? <Link className={itemClass("platform_admin", active)} href="/platform/users">{t(locale, "tab_platform_admin", "Platform Admin")}</Link> : null}
      </nav>
      <div className="sidebar-footer">
        <a className="menu-item" href="#">{t(locale, "sidebar_docs", "Documentation")}</a>
        <a className="menu-item" href="#">{t(locale, "sidebar_support", "Support")}</a>
        <button className="menu-item" onClick={() => void handleLogout()} style={{ textAlign: "left" }}>
          {t(locale, "sidebar_logout", "Log Out")}
        </button>
      </div>
    </aside>
  );
}
