import {
  AdAccount,
  AdAccountSyncDiagnostic,
  AdAccountSyncRunResponse,
  IntegrationProvider,
  Overview,
} from "./types";

export const DATA_STALE_AFTER_HOURS = 48;
const DATA_STALE_AFTER_MS = DATA_STALE_AFTER_HOURS * 60 * 60 * 1000;

export type DataFreshnessState =
  | "current"
  | "stale"
  | "never_synced"
  | "insufficient_data"
  | "retry_scheduled"
  | "error";

export type DataFreshnessMeta = {
  label: string;
  description: string;
  tone: "good" | "warn" | "bad";
};

function timestampState(
  value?: string | null,
  now = Date.now(),
): "current" | "stale" | "never_synced" | "insufficient_data" {
  if (!value) return "never_synced";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp) || timestamp > now + 5 * 60 * 1000) return "insufficient_data";
  return now - timestamp > DATA_STALE_AFTER_MS ? "stale" : "current";
}

function dataDateState(value?: unknown, now = Date.now()): DataFreshnessState | null {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return null;
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T23:59:59.999Z` : raw;
  return timestampState(normalized, now);
}

export function accountDataFreshness(account: AdAccount, now = Date.now()): DataFreshnessState {
  if (String(account.sync_status || "").toLowerCase() === "error") return "error";
  if (account.status !== "active") return "insufficient_data";

  const exactDataDate = account.metadata?.latest_data_date ?? account.metadata?.last_data_at;
  const exactState = dataDateState(exactDataDate, now);
  if (exactState) {
    if (exactState !== "current") return exactState;
    return account.sync_status === "success" ? "current" : "insufficient_data";
  }

  // A request heartbeat is not evidence that fresh metric rows were received.
  const heartbeat = timestampState(account.last_sync_at, now);
  if (heartbeat !== "current") return heartbeat;
  return "insufficient_data";
}

export function providerDataFreshness(provider: IntegrationProvider, now = Date.now()): DataFreshnessState {
  if (!provider.sync_ready || provider.status === "error" || provider.status === "disconnected") return "error";

  const exactState = dataDateState(provider.latest_data_date, now);
  if (exactState && exactState !== "current") return exactState;
  if (!exactState) {
    const heartbeat = timestampState(provider.last_successful_sync_at, now);
    if (heartbeat !== "current") return heartbeat;
  }

  if (provider.status !== "healthy" || provider.last_error_safe || !provider.rows_present) {
    return "insufficient_data";
  }
  return "current";
}

export function diagnosticDataFreshness(
  diagnostic: AdAccountSyncDiagnostic,
  now = Date.now(),
): DataFreshnessState {
  if (diagnostic.sync_state === "error") return "error";
  if (diagnostic.sync_state === "retry_scheduled") return "retry_scheduled";

  const timestamp = timestampState(diagnostic.last_sync_at, now);
  if (timestamp !== "current") return timestamp;

  return diagnostic.sync_state === "healthy" && diagnostic.last_job_status === "success"
    ? "current"
    : "insufficient_data";
}

export function aggregateAccountFreshness(
  accounts: AdAccount[],
  options?: { hasMetricRows?: boolean },
): DataFreshnessState {
  if (!accounts.length) return "insufficient_data";
  const states = accounts.map((account) => accountDataFreshness(account));
  if (states.includes("error")) return "error";
  if (states.includes("retry_scheduled")) return "retry_scheduled";
  if (states.includes("never_synced")) return "never_synced";
  if (states.includes("stale")) return "stale";
  if (states.includes("insufficient_data")) return "insufficient_data";
  if (options?.hasMetricRows === false) return "insufficient_data";
  return "current";
}

export function overviewDataFreshness(overview?: Overview | null): DataFreshnessState {
  const quality = overview?.data_quality;
  if (!quality) return "insufficient_data";
  if (quality.status === "fresh" && quality.rows_present && quality.row_count > 0) return "current";
  if (quality.status === "stale") return "stale";
  return "insufficient_data";
}

export function metricRowsFreshness(rows: Array<{ date: string }>, now = Date.now()): DataFreshnessState {
  if (!rows.length) return "insufficient_data";
  const latest = rows
    .map((row) => String(row.date || ""))
    .filter(Boolean)
    .sort()
    .at(-1);
  if (!latest) return "insufficient_data";
  return timestampState(`${latest.slice(0, 10)}T23:59:59.999Z`, now);
}

export function dataFreshnessMeta(state: DataFreshnessState): DataFreshnessMeta {
  if (state === "current") {
    return {
      label: "Данные актуальны",
      description: "Свежие строки метрик подтверждены и находятся в допустимом окне актуальности.",
      tone: "good",
    };
  }
  if (state === "stale") {
    return {
      label: "Данные устарели",
      description: "Последние строки метрик старше допустимого порога. Обновите данные перед принятием решений.",
      tone: "warn",
    };
  }
  if (state === "never_synced") {
    return {
      label: "Данные ещё не загружались",
      description: "У аккаунта нет ни одной подтверждённой успешной синхронизации с данными.",
      tone: "warn",
    };
  }
  if (state === "retry_scheduled") {
    return {
      label: "Повтор запланирован",
      description: "Предыдущая попытка завершилась ошибкой. Проверьте запланированный повторный запуск.",
      tone: "warn",
    };
  }
  if (state === "error") {
    return {
      label: "Ошибка обновления",
      description: "Последняя попытка завершилась ошибкой. Эти данные нельзя считать актуальными.",
      tone: "bad",
    };
  }
  return {
    label: "Недостаточно данных",
    description: "Нет подтверждения успешной и свежей загрузки данных для выбранного контура.",
    tone: "warn",
  };
}

export function syncRunFeedback(result: AdAccountSyncRunResponse): {
  message: string;
  tone: "success" | "info" | "error";
} {
  const noData = result.jobs.filter(
    (job) => job.status === "success" && (job.records_synced === 0 || job.request_meta?.empty_response === true),
  ).length;
  const message = `Запрошено ${result.requested}, обработано ${result.processed}: с данными ${Math.max(0, result.success - noData)}, без данных ${noData}, с ошибкой ${result.failed}, пропущено ${result.skipped}.`;
  if (result.failed > 0) {
    return { message, tone: result.success > 0 ? "info" : "error" };
  }
  if (
    noData > 0 ||
    result.processed === 0 ||
    result.success === 0 ||
    result.skipped > 0 ||
    result.success < result.processed
  ) {
    return { message, tone: "info" };
  }
  return { message, tone: "success" };
}
