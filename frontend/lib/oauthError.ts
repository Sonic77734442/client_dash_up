const USER_CANCELLED_ERRORS = new Set(["access_denied", "user_denied"]);
const SAFE_OAUTH_ERROR_MESSAGES: Record<string, string> = {
  access_not_granted:
    "Сервис не получил необходимые разрешения. Подключите платформу ещё раз и подтвердите доступ к рекламным аккаунтам.",
  access_pending:
    "Доступ к этой учётной записи отключён или приостановлен. Обратитесь к администратору платформы.",
  account_link_required:
    "Не удалось безопасно связать Facebook с учётной записью. Войдите другим способом или обратитесь к администратору платформы.",
  facebook_auth_not_configured:
    "Вход через Facebook сейчас недоступен. Используйте другой способ входа или обратитесь к администратору.",
  facebook_migration_required:
    "Мы обновили вход через Facebook. Перенесите существующую привязку — ваши роль, клиенты и данные сохранятся.",
  facebook_migration_identity_not_found:
    "Старая Facebook-привязка не найдена. Войдите по email или обратитесь к администратору платформы.",
  facebook_identity_conflict:
    "Эта Facebook-учётная запись уже связана с другим пользователем. Войдите по email или обратитесь к администратору платформы.",
};

export function oauthErrorMessage(code: string | null | undefined): string {
  const normalized = String(code || "").trim().toLowerCase();
  if (!normalized) return "";
  if (USER_CANCELLED_ERRORS.has(normalized)) {
    return "Вы отменили предоставление доступа.";
  }
  if (SAFE_OAUTH_ERROR_MESSAGES[normalized]) {
    return SAFE_OAUTH_ERROR_MESSAGES[normalized];
  }
  return "Не удалось завершить вход через сервис. Попробуйте ещё раз.";
}
