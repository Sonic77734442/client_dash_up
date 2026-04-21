"use client";

import Link from "next/link";
import { useSessionContext } from "../hooks/useSessionContext";

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
  const showPlatformAdmin = Boolean(context?.valid) && context?.role === "admin";
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

  async function handleLogout() {
    try {
      const apiBase = (localStorage.getItem("ops_api_base") || defaultApiBase).replace(/\/$/, "");
      const token = localStorage.getItem("ops_session_token") || "";
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
        <Link className={itemClass("dashboard", active)} href="/">Dashboard</Link>
        <Link className={itemClass("accounts", active)} href="/accounts">Accounts</Link>
        <Link className={itemClass("traffic", active)} href="/traffic">Traffic</Link>
        <Link className={itemClass("integrations", active)} href="/integrations">Integrations</Link>
        <Link className={itemClass("sync_monitor", active)} href="/sync-monitor">Sync Monitor</Link>
        <Link className={itemClass("budgets", active)} href="/budgets">Budgets</Link>
        <Link className={itemClass("clients", active)} href="/clients">Clients</Link>
        {showPlatformAdmin ? <Link className={itemClass("platform_admin", active)} href="/platform/users">Platform Admin</Link> : null}
      </nav>
      <div className="sidebar-footer">
        <a className="menu-item" href="#">Documentation</a>
        <a className="menu-item" href="#">Support</a>
        <button className="menu-item" onClick={() => void handleLogout()} style={{ textAlign: "left" }}>
          Log Out
        </button>
      </div>
    </aside>
  );
}
