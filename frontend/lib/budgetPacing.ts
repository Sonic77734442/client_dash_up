export type BudgetPace = "on_track" | "overspending" | "underspending" | "unknown";

/** Compare the selected interval's spend with its share of the full budget. */
export function budgetPace(input: {
  spend: number;
  amount: number;
  start?: string;
  end?: string;
  rangeFrom: string;
  rangeTo: string;
  asOf: string;
}): BudgetPace {
  const day = (value: string | undefined) => value && /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? Date.parse(`${value}T00:00:00Z`) / 86_400_000
    : NaN;
  const start = day(input.start);
  const end = day(input.end);
  const from = Math.max(start, day(input.rangeFrom));
  const to = Math.min(end, day(input.rangeTo), day(input.asOf));
  if (![start, end, from, to, input.spend, input.amount].every(Number.isFinite)
    || end < start || input.amount < 0 || input.spend < 0) return "unknown";
  if (to < from) return "unknown";
  const expected = Math.round(input.amount * ((to - from + 1) / (end - start + 1)) * 100) / 100;
  if (expected === 0) return input.spend > 0 ? "overspending" : "on_track";
  if (input.spend > expected * 1.1) return "overspending";
  if (input.spend < expected * 0.9) return "underspending";
  return "on_track";
}
