const USER_CANCELLED_ERRORS = new Set(["access_denied", "user_denied"]);

export function oauthErrorMessage(code: string | null | undefined): string {
  const normalized = String(code || "").trim().toLowerCase();
  if (!normalized) return "";
  if (USER_CANCELLED_ERRORS.has(normalized)) {
    return "Вы отменили предоставление доступа.";
  }
  return "Не удалось завершить вход через сервис. Попробуйте ещё раз.";
}
