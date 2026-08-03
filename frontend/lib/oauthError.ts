const USER_CANCELLED_ERRORS = new Set(["access_denied", "user_denied"]);
const SAFE_OAUTH_ERROR_MESSAGES: Record<string, string> = {
  access_not_granted:
    "Администратор ещё не предоставил вам доступ к платформе. Обратитесь к администратору.",
  access_pending:
    "Ваша учётная запись пока не активна. Дождитесь подтверждения администратора.",
  account_link_required:
    "Вход через Facebook пока не связан с вашей учётной записью. Обратитесь к администратору.",
  facebook_auth_not_configured:
    "Вход через Facebook сейчас недоступен. Используйте другой способ входа или обратитесь к администратору.",
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
