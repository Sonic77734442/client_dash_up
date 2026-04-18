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
      await fetch(`${apiBase}/auth/logout`, {
        method: "POST",
        headers,
        credentials: "include",
      });
    } catch {
      // noop
    } finally {
      localStorage.removeItem("ops_session_token");
      window.dispatchEvent(new Event("ops-session-updated"));
      window.location.href = "/login";
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
        {showPlatformAdmin ? <Link className={itemClass("platform_admin", active)} href="/platform/agencies">Platform Admin</Link> : null}
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
