import { normalizeListPayload } from "./listPayload";

export type ProviderConfig = {
  provider: string;
  enabled?: boolean;
  client_id?: string | null;
  redirect_uri?: string | null;
  updated_at?: string | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function optionalString(value: unknown): string | null | undefined {
  if (value === null) return null;
  if (value === undefined) return undefined;
  return typeof value === "string" ? value : undefined;
}

export function normalizeProviderConfigs(payload: unknown): ProviderConfig[] {
  const rows = normalizeListPayload(payload, (_row): _row is unknown => true, "OAuth-провайдеров");

  return rows.map((row, index) => {
    if (!isRecord(row) || typeof row.provider !== "string" || !row.provider.trim()) {
      throw new Error(`Некорректная конфигурация OAuth-провайдера в строке ${index + 1}`);
    }
    if (row.enabled !== undefined && typeof row.enabled !== "boolean") {
      throw new Error(`Некорректный статус OAuth-провайдера ${row.provider}`);
    }

    const redirectUri = optionalString(row.redirect_uri);
    const clientId = optionalString(row.client_id);
    const updatedAt = optionalString(row.updated_at);
    if (
      (row.redirect_uri !== undefined && redirectUri === undefined) ||
      (row.client_id !== undefined && clientId === undefined) ||
      (row.updated_at !== undefined && updatedAt === undefined)
    ) {
      throw new Error(`Некорректные поля OAuth-провайдера ${row.provider}`);
    }

    return {
      provider: row.provider.trim(),
      enabled: row.enabled as boolean | undefined,
      client_id: clientId,
      redirect_uri: redirectUri,
      updated_at: updatedAt,
    };
  });
}
