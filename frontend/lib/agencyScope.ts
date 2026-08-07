import type { AdAccount, IntegrationConnection, IntegrationProvider, IntegrationsOverview } from "./types";

function providerKey(value: string) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "facebook") return "meta";
  if (normalized === "google_ads") return "google";
  return normalized;
}

function latestDate(values: Array<string | null | undefined>) {
  return values.filter((value): value is string => Boolean(value)).sort().at(-1) || null;
}

export function scopeIntegrationsOverview(
  overview: IntegrationsOverview,
  allAccounts: AdAccount[],
  allConnections: IntegrationConnection[],
  agencyId: string,
  clientIds: string[],
): { overview: IntegrationsOverview; accounts: AdAccount[]; connections: IntegrationConnection[] } {
  const allowedClientIds = new Set(clientIds);
  const accounts = allAccounts.filter((account) => allowedClientIds.has(account.client_id));
  const connections = allConnections.filter((connection) => (
    (connection.scope_type === "agency" && connection.scope_id === agencyId)
    || (connection.scope_type === "client" && !!connection.scope_id && allowedClientIds.has(connection.scope_id))
  ));
  const providers = overview.providers.map((provider) => {
    const key = providerKey(provider.provider);
    const providerAccounts = accounts.filter((account) => providerKey(account.platform) === key);
    const providerConnections = connections.filter(
      (connection) => providerKey(connection.provider) === key && connection.status === "active",
    );
    const activeAccounts = providerAccounts.filter((account) => account.status === "active");
    const errorAccounts = activeAccounts.filter((account) => account.sync_status === "error");
    const neverSynced = activeAccounts.filter((account) => !account.last_sync_at);
    const accountsWithData = activeAccounts.filter((account) => (
      typeof account.metadata?.latest_data_date === "string"
      || typeof account.metadata?.last_data_at === "string"
    ));
    const connected = providerConnections.length > 0;
    const latestDataDate = latestDate(
      accountsWithData.map((account) => String(
        account.metadata?.latest_data_date || account.metadata?.last_data_at || "",
      )),
    );
    return {
      ...provider,
      status: connected ? (errorAccounts.length ? "error" : neverSynced.length ? "warning" : "healthy") : "disconnected",
      status_reason: connected ? null : "Для выбранного агентства подключение не настроено",
      auth_state: connected ? "configured" : "missing",
      connection_sources: providerConnections.map((connection) => connection.connection_key),
      identity_linked_users: providerConnections.length,
      sync_ready: connected,
      sync_readiness_reason: connected ? null : "Подключите платформу для выбранного агентства",
      linked_accounts_count: providerAccounts.length,
      active_accounts_count: activeAccounts.length,
      successfully_synced_accounts_count: activeAccounts.filter((account) => account.sync_status === "success").length,
      accounts_with_data_count: accountsWithData.length,
      error_accounts_count: errorAccounts.length,
      never_synced_accounts_count: neverSynced.length,
      stale_accounts_count: 0,
      assignment_conflict_accounts_count: providerAccounts.filter(
        (account) => account.metadata?.assignment_conflict === true,
      ).length,
      coverage_percent: activeAccounts.length ? (accountsWithData.length / activeAccounts.length) * 100 : 0,
      rows_present: accountsWithData.length > 0,
      latest_data_date: latestDataDate,
      affected_clients_count: new Set(providerAccounts.map((account) => account.client_id)).size,
      last_heartbeat_at: latestDate(activeAccounts.map((account) => account.last_sync_at)),
      last_successful_sync_at: latestDate(
        activeAccounts.filter((account) => account.sync_status === "success").map((account) => account.last_sync_at),
      ),
      last_error_time: latestDate(
        activeAccounts.filter((account) => account.sync_status === "error").map((account) => account.last_sync_at),
      ),
    } satisfies IntegrationProvider;
  });
  const scopedOverview: IntegrationsOverview = {
    summary: {
      ...overview.summary,
      connected_providers: providers.filter((provider) => provider.auth_state === "configured").length,
      healthy_connections: providers.filter((provider) => provider.status === "healthy").length,
      warning_connections: providers.filter((provider) => provider.status === "warning").length,
      critical_issues: providers.filter((provider) => provider.status === "error").length,
      active_nodes: accounts.filter((account) => account.status === "active").length,
      assignment_conflict_accounts: providers.reduce(
        (sum, provider) => sum + Number(provider.assignment_conflict_accounts_count || 0),
        0,
      ),
      total_errors_24h: 0,
    },
    providers,
    // Events have no tenant identifier in the current read contract, so showing the union would leak another agency.
    events: [],
  };
  return { overview: scopedOverview, accounts, connections };
}
