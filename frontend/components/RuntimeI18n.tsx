"use client";

import { useEffect, useMemo } from "react";
import { useLocale } from "../hooks/useLocale";

const RU_MAP: Record<string, string> = {
  "Sign In": "Вход",
  "Sign in using your connected provider.": "Войдите через подключенный способ авторизации.",
  "Internal token login": "Вход по внутреннему токену",
  "API base": "API адрес",
  "Session token": "Токен сессии",
  "Sign In with Password": "Войти по паролю",
  "Email and password": "Email и пароль",
  "Email": "Email",
  "Password": "Пароль",
  "At least 8 characters": "Минимум 8 символов",
  "Agency invite": "Инвайт агентства",
  "Your name (optional)": "Ваше имя (необязательно)",
  "Set password": "Задайте пароль",
  "Accept Invite": "Принять инвайт",
  "Accepting...": "Принимаем...",
  "OAuth providers": "OAuth провайдеры",
  "Continue with Facebook": "Продолжить через Facebook",
  "Continue with Google": "Продолжить через Google",

  "Dashboard": "Дашборд",
  "Accounts": "Аккаунты",
  "Traffic": "Трафик",
  "Integrations": "Интеграции",
  "Sync Monitor": "Синк Монитор",
  "Budgets": "Бюджеты",
  "Clients": "Клиенты",
  "Platform Admin": "Админка Платформы",
  "Documentation": "Документация",
  "Support": "Поддержка",
  "Log Out": "Выйти",
  "Save": "Сохранить",
  "Refresh": "Обновить",
  "Close": "Закрыть",
  "Cancel": "Отмена",
  "Create": "Создать",
  "Delete": "Удалить",
  "Deactivate": "Деактивировать",
  "Activate": "Активировать",
  "Remove": "Убрать",
  "Reconnect": "Переподключить",
  "Disconnect": "Отключить",
  "Issue Invite": "Отправить инвайт",
  "Resend": "Переотправить",
  "Revoke": "Отозвать",

  "Platform Users": "Пользователи Платформы",
  "Create User": "Создать пользователя",
  "User created": "Пользователь создан",
  "User updated": "Пользователь обновлен",
  "User deleted": "Пользователь удален",
  "Search name/email/role/status": "Поиск по имени/email/роли/статусу",
  "No users.": "Пользователей нет.",
  "Name": "Имя",
  "Role": "Роль",
  "Status": "Статус",
  "Updated": "Обновлено",
  "Actions": "Действия",

  "Platform Admin: Agencies": "Админка Платформы: Агентства",
  "Provision agencies, attach members, and grant tenant access.": "Создавайте агентства, назначайте участников и выдавайте доступ к тенантам.",
  "Create Agency": "Создать агентство",
  "Agencies Registry": "Реестр агентств",
  "Agency Details": "Детали агентства",
  "Members": "Участники",
  "Active Members": "Активные участники",
  "Step 2: Add Member": "Шаг 2: Добавить участника",
  "Step 3: Invite Agency User": "Шаг 3: Пригласить пользователя агентства",
  "No members yet.": "Участников пока нет.",
  "No invites yet.": "Инвайтов пока нет.",
  "Agency created": "Агентство создано",
  "Agency deleted": "Агентство удалено",
  "Member updated": "Участник обновлен",
  "Member deactivated": "Участник деактивирован",
  "Member removed": "Участник удален",
  "Invite issued": "Инвайт отправлен",
  "Invite revoked": "Инвайт отозван",
  "Invite resent": "Инвайт переотправлен",
  "Invite link copied": "Ссылка инвайта скопирована",

  "Platform Alerts": "Алерты Платформы",
  "open": "открыт",
  "acked": "подтвержден",
  "resolved": "решен",
  "critical": "критично",
  "high": "высокий",
  "medium": "средний",
  "low": "низкий",

  "Provider Connection State": "Состояние подключений провайдеров",
  "Connect Google": "Подключить Google",
  "Connect Facebook": "Подключить Facebook",
  "Sync All": "Синхронизировать всё",
  "Discover Accounts": "Найти аккаунты",
  "Auto (Discovery Inbox)": "Авто (Discovery Inbox)",
  "Select client": "Выберите клиента",
  "Target client for imported accounts": "Клиент для импортируемых аккаунтов",
  "Optional target client for imported accounts": "Необязательный клиент для импортируемых аккаунтов",
  "Client role is read-only here. Provider connect/discovery/sync is managed by agency/admin.": "Роль клиента здесь только для просмотра. Подключение/поиск/синк выполняют agency/admin.",
  "Account Sync Diagnostics": "Диагностика синка аккаунтов",
  "Per-account health with safe error reasons and next action hints.": "Состояние по каждому аккаунту с безопасными причинами ошибок и подсказками.",

  "No diagnostics rows.": "Нет строк диагностики.",
  "No connections visible for current role.": "Для текущей роли нет доступных подключений.",

  "Billing": "Биллинг",
  "Reports": "Отчеты",
  "Portal": "Портал",
  "Client Portal": "Портал клиента",
};

const ATTRS = ["placeholder", "title", "aria-label"] as const;

function translateValue(value: string, ru: boolean, reverseMap: Record<string, string>): string {
  const trimmed = value.trim();
  if (!trimmed) return value;
  const translated = ru ? (RU_MAP[trimmed] || trimmed) : (reverseMap[trimmed] || trimmed);
  if (translated === trimmed) return value;
  const leading = value.match(/^\s*/)?.[0] || "";
  const trailing = value.match(/\s*$/)?.[0] || "";
  return `${leading}${translated}${trailing}`;
}

export function RuntimeI18n() {
  const { locale } = useLocale();
  const reverseMap = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [en, ru] of Object.entries(RU_MAP)) out[ru] = en;
    return out;
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const ru = locale === "ru";
    let applying = false;

    const applyTranslations = () => {
      if (applying) return;
      applying = true;
      try {
        const root = document.body;
        if (!root) return;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node = walker.nextNode();
        while (node) {
          const parentTag = (node.parentElement?.tagName || "").toUpperCase();
          if (parentTag === "SCRIPT" || parentTag === "STYLE" || parentTag === "NOSCRIPT") {
            node = walker.nextNode();
            continue;
          }
          const current = node.nodeValue || "";
          const next = translateValue(current, ru, reverseMap);
          if (next !== current) node.nodeValue = next;
          node = walker.nextNode();
        }

        const elements = root.querySelectorAll<HTMLElement>("*");
        for (const el of elements) {
          for (const attr of ATTRS) {
            const current = el.getAttribute(attr);
            if (!current) continue;
            const next = translateValue(current, ru, reverseMap);
            if (next !== current) el.setAttribute(attr, next);
          }
        }
      } finally {
        applying = false;
      }
    };

    applyTranslations();
    const observer = new MutationObserver(() => applyTranslations());
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [...ATTRS],
    });
    return () => observer.disconnect();
  }, [locale, reverseMap]);

  return null;
}
