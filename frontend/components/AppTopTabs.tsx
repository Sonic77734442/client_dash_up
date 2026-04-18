"use client";

import Link from "next/link";
import { useSessionContext } from "../hooks/useSessionContext";

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

  return (
    <div className="view-tabs">
      <Link className={cls(active === "dashboard")} href="/">Dashboard</Link>
      <Link className={cls(active === "accounts")} href="/accounts">Accounts</Link>
      <Link className={cls(active === "traffic")} href="/traffic">Traffic</Link>
      <Link className={cls(active === "integrations")} href="/integrations">Integrations</Link>
      <Link className={cls(active === "sync_monitor")} href="/sync-monitor">Sync Monitor</Link>
      <Link className={cls(active === "budgets")} href="/budgets">Budgets</Link>
      <Link className={cls(active === "clients")} href="/clients">Clients</Link>
      {showPlatformAdmin ? <Link className={cls(active === "platform_admin")} href="/platform/agencies">Platform Admin</Link> : null}
    </div>
  );
}
