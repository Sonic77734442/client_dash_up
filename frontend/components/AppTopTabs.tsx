"use client";

import Link from "next/link";
import { useSessionContext } from "../hooks/useSessionContext";
import { useLocale } from "../hooks/useLocale";
import { Locale, t } from "../lib/i18n";

type TabKey =
  | "dashboard"
  | "accounts"
  | "traffic"
  | "integrations"
  | "sync_monitor"
  | "budgets"
  | "clients"
  | "platform_admin";

function cls(active: boolean) {
  return `view-tab ${active ? "active" : ""}`.trim();
}

export function AppTopTabs({ active }: { active: TabKey }) {
  const { context } = useSessionContext();
  const showPlatformAdmin = Boolean(context?.valid) && context?.role === "admin";
  const { locale, setLocale } = useLocale();

  return (
    <div className="view-tabs">
      <Link className={cls(active === "dashboard")} href="/">{t(locale, "tab_dashboard", "Dashboard")}</Link>
      <Link className={cls(active === "accounts")} href="/accounts">{t(locale, "tab_accounts", "Accounts")}</Link>
      <Link className={cls(active === "traffic")} href="/traffic">{t(locale, "tab_traffic", "Traffic")}</Link>
      <Link className={cls(active === "integrations")} href="/integrations">{t(locale, "tab_integrations", "Integrations")}</Link>
      <Link className={cls(active === "sync_monitor")} href="/sync-monitor">{t(locale, "tab_sync_monitor", "Sync Monitor")}</Link>
      <Link className={cls(active === "budgets")} href="/budgets">{t(locale, "tab_budgets", "Budgets")}</Link>
      <Link className={cls(active === "clients")} href="/clients">{t(locale, "tab_clients", "Clients")}</Link>
      {showPlatformAdmin ? <Link className={cls(active === "platform_admin")} href="/platform/users">{t(locale, "tab_platform_admin", "Platform Admin")}</Link> : null}
      <select
        className="locale-switch"
        value={locale}
        onChange={(e) => setLocale(e.target.value as Locale)}
        aria-label="Language"
        title="Language"
      >
        <option value="en">EN</option>
        <option value="ru">RU</option>
      </select>
    </div>
  );
}
