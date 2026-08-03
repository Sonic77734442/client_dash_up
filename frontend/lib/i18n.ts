export type Locale = "en" | "ru";

const DICT: Record<Locale, Record<string, string>> = {
  en: {
    tab_dashboard: "Dashboard",
    tab_accounts: "Accounts",
    tab_traffic: "Traffic",
    tab_integrations: "Integrations",
    tab_sync_monitor: "Sync Monitor",
    tab_budgets: "Budgets",
    tab_clients: "Clients",
    tab_platform_admin: "Platform Admin",

    sidebar_docs: "Documentation",
    sidebar_support: "Support",
    sidebar_logout: "Log Out",
  },
  ru: {
    tab_dashboard: "Центр эффективности",
    tab_accounts: "Рекламные аккаунты",
    tab_traffic: "Лиды и трафик",
    tab_integrations: "Подключения",
    tab_sync_monitor: "Синхронизация",
    tab_budgets: "Бюджеты",
    tab_clients: "Клиенты",
    tab_platform_admin: "Управление платформой",

    sidebar_docs: "Документация",
    sidebar_support: "Поддержка",
    sidebar_logout: "Выйти",
  },
};

export function t(locale: Locale, key: string, fallback: string): string {
  return DICT[locale]?.[key] || fallback;
}
