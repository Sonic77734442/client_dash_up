export function formatCurrency(value: unknown, currency: string | null | undefined): string {
  if (!currency) return "Разные валюты";
  if (value == null || !Number.isFinite(Number(value))) return "—";
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency", currency, maximumFractionDigits: 0,
    }).format(Number(value));
  } catch {
    return "Валюта не указана";
  }
}

export function sumByCurrency(rows: ReadonlyArray<{ spend: number | null; currency: string | null }>) {
  const totals = new Map<string, number>();
  for (const row of rows) {
    if (!row.currency || row.spend == null || !Number.isFinite(row.spend)) continue;
    totals.set(row.currency, (totals.get(row.currency) || 0) + row.spend);
  }
  return totals;
}

/** Account components retain legacy mixed-client totals without including another portfolio. */
export function scopedCurrencyComponents(
  clients: ReadonlyArray<{ client_id: string; spend: number | null; currency?: string | null }>,
  accounts: ReadonlyArray<{ client_id: string; spend: number; currency?: string | null }>,
) {
  return clients.flatMap((client) => {
    const components = accounts.filter((account) => account.client_id === client.client_id);
    return (components.length ? components : [client]).map((row) => ({
      spend: row.spend, currency: row.currency || null,
    }));
  });
}

export function budgetComparisonNote(reason: string | null | undefined): string {
  if (reason === "currency_mismatch") return "Валюты расхода и бюджета различаются. Сравнение с планом и прогноз недоступны.";
  if (reason === "mixed_currencies") return "Расходы включают разные валюты. Общая сумма, сравнение с планом и прогноз недоступны.";
  return "";
}
