"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useSessionContext } from "../hooks/useSessionContext";
import { useLocale } from "../hooks/useLocale";
import { t } from "../lib/i18n";
import { resolveApiBase } from "../lib/apiBase";
import { clearSessionToken, getSessionToken } from "../lib/sessionToken";

export type SidebarSection =
  | "dashboard"
  | "accounts"
  | "integrations"
  | "traffic"
  | "sync_monitor"
  | "budgets"
  | "clients"
  | "platform_admin";

type Role = "admin" | "agency" | "client";

type IconName =
  | "dashboard"
  | "inbox"
  | "alert"
  | "building"
  | "clients"
  | "access"
  | "database"
  | "sync"
  | "audit"
  | "settings"
  | "reports"
  | "accounts"
  | "leads"
  | "budgets"
  | "team"
  | "logout";

type NavItem = {
  href: string;
  label: string;
  icon: IconName;
  match?: string[];
};

type NavGroup = {
  label: string;
  items: NavItem[];
};

function SidebarIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    dashboard: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="4" rx="1.5" />
        <rect x="14" y="11" width="7" height="10" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    inbox: <><path d="M4 5.5h16v13H4z" /><path d="m4 13 4-4h8l4 4M8 13h8" /></>,
    alert: <><path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4M12 17h.01" /></>,
    building: <><rect x="4" y="3" width="16" height="18" rx="1.5" /><path d="M8 7h2M14 7h2M8 11h2M14 11h2M8 15h2M14 15h2M10 21v-3h4v3" /></>,
    clients: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
    access: <><circle cx="8" cy="15" r="4" /><path d="m11 12 8-8M15 8l3 3M17 6l3 3" /></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" /></>,
    sync: <><path d="M20 7h-5V2M20 7a8 8 0 0 0-13.66-2M4 17h5v5M4 17a8 8 0 0 0 13.66 2" /></>,
    audit: <><path d="M6 3h12v18H6zM9 7h6M9 11h6M9 15h4" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.12-1.28l2-1.55-2-3.46-2.47 1A7 7 0 0 0 14.2 5.4L13.85 3h-4l-.35 2.4a7 7 0 0 0-2.2 1.3l-2.48-1-2 3.46 2 1.55A7 7 0 0 0 4.7 12c0 .44.04.87.12 1.28l-2 1.55 2 3.46 2.47-1a7 7 0 0 0 2.21 1.3l.35 2.4h4l.35-2.4a7 7 0 0 0 2.2-1.3l2.48 1 2-3.46-2-1.55c.08-.41.12-.84.12-1.28Z" /></>,
    reports: <><path d="M5 3h10l4 4v14H5zM15 3v5h5M9 17v-3M13 17v-6M17 17v-4" /></>,
    accounts: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 10h18M7 15h3" /></>,
    leads: <><circle cx="9" cy="8" r="3" /><path d="M3 20v-1a6 6 0 0 1 12 0v1M16 8h5M18.5 5.5v5" /></>,
    budgets: <><circle cx="12" cy="12" r="9" /><path d="M16 8.5c-.8-.7-2-1-3.2-1-1.8 0-3.3.8-3.3 2.2 0 3.6 6.7 1.5 6.7 5 0 1.4-1.4 2.3-3.4 2.3-1.4 0-2.8-.5-3.7-1.3M12.7 5.5v13" /></>,
    team: <><circle cx="8" cy="8" r="3" /><circle cx="17" cy="8" r="3" /><path d="M2 20v-1a6 6 0 0 1 12 0v1M12 20v-1a5 5 0 0 1 10 0v1" /></>,
    logout: <><path d="M10 17l5-5-5-5M15 12H3M14 3h6v18h-6" /></>,
  };

  return (
    <svg
      viewBox="0 0 24 24"
      width="17"
      height="17"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const parts = document.cookie ? document.cookie.split(";") : [];
  const prefix = `${name}=`;
  for (const part of parts) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return "";
}

function isCurrentPath(pathname: string, item: NavItem) {
  if (item.href === "/" || item.href === "/platform" || item.href === "/portal") {
    return pathname === item.href;
  }
  if (pathname === item.href || pathname.startsWith(`${item.href}/`)) return true;
  return (item.match || []).some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

function menuForRole(role: Role): NavGroup[] {
  if (role === "admin") {
    return [
      {
        label: "Ежедневная работа",
        items: [
          { href: "/platform", label: "Центр решений", icon: "dashboard" },
          { href: "/platform/users", label: "Запросы и пользователи", icon: "inbox" },
          { href: "/platform/alerts", label: "Инциденты", icon: "alert" },
        ],
      },
      {
        label: "Управление",
        items: [
          { href: "/platform/agencies", label: "Агентства", icon: "building" },
          { href: "/clients", label: "Клиенты", icon: "clients", match: ["/client"] },
          { href: "/platform/access", label: "Карта доступов", icon: "access" },
        ],
      },
      {
        label: "Платформа",
        items: [
          { href: "/integrations", label: "Данные и подключения", icon: "database" },
          { href: "/sync-monitor", label: "Синхронизация", icon: "sync" },
          { href: "/platform/audit", label: "Журнал действий", icon: "audit" },
          { href: "/platform/settings", label: "Настройки", icon: "settings" },
        ],
      },
    ];
  }

  if (role === "client") {
    return [
      {
        label: "Результаты",
        items: [
          { href: "/portal", label: "Главное", icon: "dashboard" },
          { href: "/portal/advertising", label: "Реклама", icon: "accounts" },
          { href: "/portal/leads", label: "Лиды", icon: "leads" },
          { href: "/portal/reports", label: "Отчёты", icon: "reports" },
        ],
      },
      {
        label: "Работа агентства",
        items: [
          { href: "/portal/changes", label: "Что изменилось", icon: "alert" },
          { href: "/portal/plan", label: "План действий", icon: "audit" },
        ],
      },
    ];
  }

  return [
    {
      label: "Ежедневная работа",
      items: [
        { href: "/", label: "Центр эффективности", icon: "dashboard" },
        { href: "/clients", label: "Клиенты", icon: "clients", match: ["/client"] },
        { href: "/agency/actions", label: "Отклонения", icon: "alert" },
        { href: "/agency/reports", label: "Отчёты", icon: "reports" },
      ],
    },
    {
      label: "Данные",
      items: [
        { href: "/accounts", label: "Рекламные аккаунты", icon: "accounts" },
        { href: "/traffic", label: "Лиды и трафик", icon: "leads" },
        { href: "/integrations", label: "Подключения", icon: "database" },
      ],
    },
    {
      label: "Агентство",
      items: [
        { href: "/budgets", label: "Бюджеты", icon: "budgets" },
        { href: "/sync-monitor", label: "Синхронизация", icon: "sync" },
        { href: "/agency/team", label: "Команда и доступы", icon: "team" },
      ],
    },
  ];
}

export function AppSidebar({
  active: _active,
  subtitle,
  className = "sidebar",
}: {
  active: SidebarSection;
  subtitle?: string;
  className?: string;
}) {
  const pathname = usePathname() || "/";
  const { context } = useSessionContext();
  const { locale } = useLocale();
  const inferredRole: Role = pathname.startsWith("/portal")
    ? "client"
    : pathname.startsWith("/platform")
    ? "admin"
    : "agency";
  const role = (context?.role || inferredRole) as Role;
  const groups = menuForRole(role);
  const defaultApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/backend";
  const tokenLoginEnabled = process.env.NEXT_PUBLIC_ENABLE_TOKEN_LOGIN === "true";
  const roleSubtitle =
    subtitle ||
    (role === "admin"
      ? "Администратор платформы"
      : role === "client"
      ? "Клиентский кабинет"
      : "Рабочее пространство агентства");

  async function handleLogout() {
    try {
      const apiBase = resolveApiBase(defaultApiBase);
      const token = tokenLoginEnabled ? getSessionToken() : "";
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
      // The local session must still be cleared when the backend is unavailable.
    } finally {
      clearSessionToken();
      localStorage.removeItem("ops_api_base");
      window.dispatchEvent(new Event("ops-session-updated"));
      window.location.replace("/login");
    }
  }

  return (
    <aside className={`${className} role-sidebar`.trim()}>
      <div className="brand" data-i18n-skip>Client Dash Up</div>
      <div className="panel-subtitle">{roleSubtitle}</div>

      <nav className="role-menu" aria-label="Основная навигация">
        {groups.map((group) => (
          <div className="role-menu-group" key={group.label}>
            <div className="role-menu-label">{group.label}</div>
            {group.items.map((item) => {
              const active = isCurrentPath(pathname, item);
              return (
                <Link
                  className={`role-menu-item ${active ? "active" : ""}`.trim()}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  key={item.href}
                >
                  <span className="role-menu-icon"><SidebarIcon name={item.icon} /></span>
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer role-sidebar-footer">
        <button className="role-menu-item role-logout" onClick={() => void handleLogout()}>
          <span className="role-menu-icon"><SidebarIcon name="logout" /></span>
          <span>{t(locale, "sidebar_logout", "Выйти")}</span>
        </button>
      </div>
    </aside>
  );
}
