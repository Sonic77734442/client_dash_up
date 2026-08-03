export type UnknownRecord = Record<string, unknown>;

export function isRecordPayload(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function hasStringFields(value: unknown, fields: readonly string[]): boolean {
  return isRecordPayload(value) && fields.every((field) => typeof value[field] === "string");
}

export function hasOptionalStringFields(value: unknown, fields: readonly string[]): boolean {
  return (
    isRecordPayload(value) &&
    fields.every(
      (field) =>
        value[field] === undefined ||
        value[field] === null ||
        typeof value[field] === "string",
    )
  );
}

export type RuntimeValidator<T> = (value: unknown) => value is T;

/**
 * Accepts both list response shapes supported by the API and validates every row.
 * A malformed response fails explicitly instead of reaching a render-time `.map`.
 */
export function normalizeListPayload<T>(
  payload: unknown,
  isItem: RuntimeValidator<T>,
  label = "данных",
): T[] {
  const rows = Array.isArray(payload)
    ? payload
    : isRecordPayload(payload) && Array.isArray(payload.items)
      ? payload.items
      : null;

  if (!rows) {
    throw new Error(`Сервис вернул некорректный список ${label}`);
  }

  return rows.map((row, index) => {
    if (!isItem(row)) {
      throw new Error(`Сервис вернул некорректную строку ${index + 1} в списке ${label}`);
    }
    return row;
  });
}
