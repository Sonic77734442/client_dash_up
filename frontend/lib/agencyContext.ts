import type { AgencyOut } from "./types";

export function resolveAgencySelection(agencies: AgencyOut[], storedAgencyId?: string | null): string {
  if (agencies.length === 1) return agencies[0].id;
  const stored = String(storedAgencyId || "").trim();
  return agencies.some((agency) => agency.id === stored) ? stored : "";
}

export function agencySelectionRequiredMessage() {
  return "У вас несколько агентств. Выберите текущее агентство в боковом меню и повторите действие.";
}
