"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState } from "react";
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

const SEARCH_ITEMS = {
  admin: [
    { label: "Центр решений", href: "/platform", hint: "Главная" },
    { label: "Запросы и пользователи", href: "/platform/users", hint: "Доступы" },
    { label: "Инциденты", href: "/platform/alerts", hint: "Платформа" },
    { label: "Агентства", href: "/platform/agencies", hint: "Управление" },
    { label: "Клиенты", href: "/clients", hint: "Управление" },
    { label: "Карта доступов", href: "/platform/access", hint: "Безопасность" },
    { label: "Источники рекламы", href: "/integrations", hint: "Подключения, аккаунты и синхронизация" },
    { label: "Журнал действий", href: "/platform/audit", hint: "Аудит" },
  ],
  agency: [
    { label: "Центр эффективности", href: "/", hint: "Главная" },
    { label: "Клиенты", href: "/clients", hint: "Портфель" },
    { label: "Отклонения", href: "/agency/actions", hint: "Действия" },
    { label: "Отчёты", href: "/agency/reports", hint: "Результаты" },
    { label: "Лиды", href: "/traffic", hint: "Данные" },
    { label: "Источники рекламы", href: "/integrations", hint: "Подключения, аккаунты и синхронизация" },
    { label: "Команда", href: "/agency/team", hint: "Доступы" },
  ],
  client: [
    { label: "Главное", href: "/portal", hint: "Результаты" },
    { label: "Реклама", href: "/portal/advertising", hint: "Показатели" },
    { label: "Лиды", href: "/portal/leads", hint: "Результаты" },
    { label: "Отчёты", href: "/portal/reports", hint: "Документы" },
    { label: "Что изменилось", href: "/portal/changes", hint: "Агентство" },
    { label: "План действий", href: "/portal/plan", hint: "Агентство" },
  ],
  solo_client: [
    { label: "Главное", href: "/portal", hint: "Мои результаты" },
    { label: "Реклама", href: "/portal/advertising", hint: "Показатели" },
    { label: "Лиды", href: "/portal/leads", hint: "Результаты" },
    { label: "Отчёты", href: "/portal/reports", hint: "Документы" },
    { label: "Источники рекламы", href: "/integrations", hint: "Подключения и аккаунты" },
    { label: "Синхронизация", href: "/sync-monitor", hint: "Обновление моих данных" },
    { label: "Мои бюджеты", href: "/budgets", hint: "Финансовый контроль" },
  ],
} as const;

export function AppTopTabs({
  active,
  contextLabel,
  sectionLabel,
}: {
  active: TabKey;
  contextLabel?: string;
  sectionLabel?: string;
}) {
  const { context } = useSessionContext();
  const { locale, setLocale } = useLocale();
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const pathname = usePathname() || "/";
  const role = (
    context?.role ||
    (pathname.startsWith("/portal") ? "client" : pathname.startsWith("/platform") ? "admin" : "agency")
  ) as "admin" | "agency" | "client" | "solo_client";
  const roleLabel =
    role === "admin"
      ? t(locale, "role_admin", "Администратор")
      : role === "client"
      ? t(locale, "role_client", "Клиент")
      : role === "solo_client"
      ? "Владелец клиента"
      : t(locale, "role_agency", "Агентство");
  const sectionLabels: Record<TabKey, string> = {
    dashboard: t(locale, "tab_dashboard", "Центр эффективности"),
    accounts: t(locale, "tab_accounts", "Рекламные аккаунты"),
    traffic: t(locale, "tab_traffic", "Лиды и трафик"),
    integrations: t(locale, "tab_integrations", "Источники рекламы"),
    sync_monitor: t(locale, "tab_sync_monitor", "Синхронизация"),
    budgets: t(locale, "tab_budgets", "Бюджеты"),
    clients: t(locale, "tab_clients", "Клиенты"),
    platform_admin: t(locale, "tab_platform_admin", "Управление платформой"),
  };
  const searchItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ru");
    const items = SEARCH_ITEMS[role];
    if (!normalized) return items.slice(0, 5);
    return items
      .filter((item) => `${item.label} ${item.hint}`.toLocaleLowerCase("ru").includes(normalized))
      .slice(0, 6);
  }, [query, role]);
  const searchPlaceholder =
    role === "admin"
      ? "Найти агентство, клиента…"
      : role === "agency"
      ? "Найти клиента, аккаунт…"
      : role === "solo_client"
      ? "Найти отчёт, подключение…"
      : "Найти отчёт, раздел…";

  return (
    <div className="context-nav" aria-label="Текущий раздел">
      <div className="context-crumbs">
        <span className="context-role">{contextLabel || roleLabel}</span>
        <span className="context-divider">/</span>
        <span className="context-section">{sectionLabel || sectionLabels[active]}</span>
      </div>
      <div className="context-actions">
        <div className="global-search">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-4-4" />
          </svg>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => window.setTimeout(() => setSearchOpen(false), 120)}
            placeholder={searchPlaceholder}
            aria-label="Глобальный поиск"
          />
          {searchOpen ? (
            <div className="global-search-results">
              {searchItems.length ? searchItems.map((item) => (
                <Link href={item.href} key={item.href} onMouseDown={(event) => event.preventDefault()}>
                  <span>{item.label}</span>
                  <small>{item.hint}</small>
                </Link>
              )) : <div className="global-search-empty">Ничего не найдено</div>}
            </div>
          ) : null}
        </div>
        <select
          className="locale-switch"
          value={locale}
          onChange={(e) => setLocale(e.target.value as Locale)}
          aria-label="Язык"
          title="Язык"
        >
          <option value="en">EN</option>
          <option value="ru">RU</option>
        </select>
        <span className="profile-pill" title={roleLabel}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
            <circle cx="12" cy="8" r="4" />
            <path d="M4.5 21a7.5 7.5 0 0 1 15 0" />
          </svg>
          <span>{roleLabel}</span>
        </span>
      </div>
    </div>
  );
}
