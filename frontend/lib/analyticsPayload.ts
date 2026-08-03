import {
  type AgencyOverview,
  type IntegrationsOverview,
  type Overview,
} from "./types";
import {
  hasStringFields,
  hasOptionalStringFields,
  isRecordPayload,
  normalizeListPayload,
} from "./listPayload";

function invalid(label: string): never {
  throw new Error(`Сервис вернул некорректный ${label}`);
}

function isPlatformBreakdown(value: unknown): value is Overview["breakdowns"]["platforms"][number] {
  return hasStringFields(value, ["platform"]);
}

function isAccountBreakdown(value: unknown): value is Overview["breakdowns"]["accounts"][number] {
  return hasStringFields(value, ["account_id", "client_id", "platform"]);
}

function isAgencyClientRow(value: unknown): value is AgencyOverview["per_client"][number] {
  return hasStringFields(value, ["client_id"]);
}

function isAgencyAccountRow(
  value: unknown,
): value is NonNullable<AgencyOverview["per_account"]>[number] {
  return hasStringFields(value, ["account_id", "client_id"]);
}

export function normalizeOverviewPayload(payload: unknown): Overview {
  if (!isRecordPayload(payload)) invalid("обзор показателей");

  const range = payload.range;
  const spendSummary = payload.spend_summary;
  const budgetSummary = payload.budget_summary;
  const breakdowns = payload.breakdowns;

  if (
    !isRecordPayload(range) ||
    !hasStringFields(range, ["date_from", "date_to"]) ||
    !hasOptionalStringFields(range, ["as_of_date", "timezone_policy"]) ||
    !isRecordPayload(spendSummary) ||
    !isRecordPayload(budgetSummary) ||
    !isRecordPayload(breakdowns)
  ) {
    invalid("обзор показателей");
  }

  const platforms = normalizeListPayload(
    breakdowns.platforms,
    isPlatformBreakdown,
    "разбивки по площадкам",
  );
  const accounts = normalizeListPayload(
    breakdowns.accounts,
    isAccountBreakdown,
    "разбивки по аккаунтам",
  );

  return {
    ...payload,
    range,
    spend_summary: spendSummary,
    budget_summary: budgetSummary,
    breakdowns: {
      ...breakdowns,
      platforms,
      accounts,
    },
  } as unknown as Overview;
}

export function normalizeAgencyOverviewPayload(payload: unknown): AgencyOverview {
  if (!isRecordPayload(payload)) invalid("обзор агентства");

  const perClient = normalizeListPayload(
    payload.per_client,
    isAgencyClientRow,
    "расходов по клиентам",
  );
  const perAccount =
    payload.per_account === undefined
      ? undefined
      : normalizeListPayload(
          payload.per_account,
          isAgencyAccountRow,
          "расходов по аккаунтам",
        );

  return {
    ...payload,
    per_client: perClient,
    ...(perAccount === undefined ? {} : { per_account: perAccount }),
  } as unknown as AgencyOverview;
}

export function normalizeIntegrationsOverviewPayload(payload: unknown): IntegrationsOverview {
  if (!isRecordPayload(payload) || !isRecordPayload(payload.summary)) {
    invalid("обзор подключений");
  }

  const providers = normalizeListPayload(
    payload.providers,
    isRecordPayload,
    "подключений",
  );
  const events = normalizeListPayload(
    payload.events,
    isRecordPayload,
    "событий подключений",
  );

  return {
    ...payload,
    summary: payload.summary,
    providers,
    events,
  } as unknown as IntegrationsOverview;
}
