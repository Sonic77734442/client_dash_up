export function calculateClientRiskScore(
  usage: number | null,
  spend: number,
  maxComparableSpend: number,
  includeSpendComponent: boolean,
) {
  const riskBase = usage == null ? 58 : usage;
  const spendComponent = includeSpendComponent
    ? (Math.max(0, spend) / Math.max(1, maxComparableSpend)) * 12
    : 0;
  return Math.max(1, Math.min(99, Math.round(riskBase + spendComponent)));
}
