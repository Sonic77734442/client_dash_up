export type Client = { id: string; name: string };
export type ClientOut = {
  id: string;
  name: string;
  legal_name?: string | null;
  status?: string;
  default_currency?: string;
  timezone?: string | null;
  notes?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type AccountBreakdown = {
  account_id: string;
  client_id: string;
  name: string;
  platform: string;
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  ctr: number;
  cpc: number;
  cpm: number;
};

export type PlatformBreakdown = {
  platform: string;
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  ctr: number;
  cpc: number;
  cpm: number;
};

export type Overview = {
  range: { date_from: string; date_to: string; as_of_date: string; timezone_policy: string };
  scope: { client_id: string | null; account_id: string | null };
  spend_summary: { spend: number; impressions: number; clicks: number; conversions: number; ctr: number; cpc: number; cpm: number };
  budget_summary: {
    budget: number | null;
    spend: number;
    remaining: number | null;
    usage_percent: number | null;
    expected_spend_to_date: number | null;
    forecast_spend: number | null;
    pace_status: string;
    pace_delta: number | null;
    pace_delta_percent: number | null;
  };
  breakdowns: {
    platforms: PlatformBreakdown[];
    accounts: AccountBreakdown[];
  };
};

export type AdStat = {
  id?: string;
  ad_account_id?: string;
  date: string;
  platform: string;
  impressions?: number;
  clicks?: number;
  spend: number;
  conversions?: number | null;
};
export type Budget = {
  id?: string;
  client_id: string;
  scope: "client" | "account";
  account_id?: string | null;
  amount: string;
  currency?: string;
  period_type?: "monthly" | "custom";
  start_date?: string;
  end_date?: string;
  status?: "active" | "archived";
  version?: number;
  note?: string | null;
  updated_at: string;
  created_at?: string;
};
export type AdAccount = {
  id: string;
  client_id: string;
  platform: string;
  external_account_id: string;
  name: string;
  currency: string;
  timezone?: string | null;
  status: string;
  metadata?: Record<string, unknown> | null;
  last_sync_at?: string | null;
  sync_status?: "success" | "error" | null;
  sync_error?: string | null;
  sync_error_code?: string | null;
  sync_error_category?: string | null;
  sync_retryable?: boolean | null;
  sync_next_retry_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type AdAccountSyncJob = {
  id: string;
  ad_account_id: string;
  provider: string;
  status: "success" | "error";
  started_at: string;
  finished_at?: string | null;
  records_synced: number;
  error_message?: string | null;
  error_code?: string | null;
  error_category?: string | null;
  retryable?: boolean;
  attempt?: number;
  next_retry_at?: string | null;
  request_meta?: Record<string, unknown> | null;
  created_by?: string | null;
  created_at: string;
};

export type AdAccountSyncRunResponse = {
  requested: number;
  processed: number;
  skipped: number;
  success: number;
  failed: number;
  retry_scheduled: number;
  started_at: string;
  finished_at: string;
  jobs: AdAccountSyncJob[];
};

export type AdAccountSyncDiagnostic = {
  ad_account_id: string;
  client_id: string;
  client_name?: string | null;
  platform: string;
  account_name: string;
  account_status: "active" | "inactive" | "archived";
  sync_state: "healthy" | "error" | "retry_scheduled" | "never_synced";
  diagnostic_message: string;
  action_hint: string;
  last_sync_at?: string | null;
  last_job_id?: string | null;
  last_job_status?: "success" | "error" | null;
  records_synced: number;
  error_code?: string | null;
  error_category?: string | null;
  retryable: boolean;
  attempt: number;
  next_retry_at?: string | null;
};

export type AdAccountSyncDiagnosticsResponse = {
  summary: {
    total_accounts: number;
    healthy: number;
    error: number;
    retry_scheduled: number;
    never_synced: number;
  };
  items: AdAccountSyncDiagnostic[];
};

export type AdAccountDiscoverResponse = {
  requested_provider: string;
  client_id: string;
  discovered: number;
  created: number;
  updated: number;
  skipped: number;
  providers_attempted: string[];
  providers_failed: Record<string, string>;
  items: AdAccount[];
};

export type IntegrationProvider = {
  provider: string;
  status: "healthy" | "warning" | "error" | "disconnected";
  status_reason?: string | null;
  auth_state: "configured" | "missing" | "disabled";
  token_hint?: string | null;
  connection_sources: string[];
  missing_requirements: string[];
  identity_linked_users: number;
  sync_ready: boolean;
  sync_readiness_reason?: string | null;
  scopes: string[];
  linked_accounts_count: number;
  affected_clients_count: number;
  last_heartbeat_at?: string | null;
  last_successful_sync_at?: string | null;
  last_error_time?: string | null;
  last_error_safe?: string | null;
  reconnect_available: boolean;
};

export type IntegrationEvent = {
  provider: string;
  level: "success" | "warning" | "error";
  title: string;
  message: string;
  occurred_at: string;
  sync_job_id?: string | null;
};

export type IntegrationsOverview = {
  summary: {
    connected_providers: number;
    healthy_connections: number;
    warning_connections: number;
    critical_issues: number;
    active_nodes: number;
    total_errors_24h: number;
  };
  providers: IntegrationProvider[];
  events: IntegrationEvent[];
};

export type SessionContext = {
  valid: boolean;
  reason?: string | null;
  session_id?: string | null;
  user_id?: string | null;
  role?: "admin" | "agency" | "client" | null;
  global_access: boolean;
  access_scope?: "all" | "assigned" | null;
  accessible_client_ids: string[];
  expires_at?: string | null;
};

export type AuthUser = {
  id: string;
  email?: string | null;
  name: string;
  role: "admin" | "agency" | "client";
  status: "active" | "inactive";
  created_at?: string;
  updated_at?: string;
};

export type AuthMeResponse = {
  user: AuthUser;
  session: SessionContext;
};

export type AgencyOut = {
  id: string;
  name: string;
  slug: string;
  status: "active" | "suspended";
  plan: string;
  notes?: string | null;
  created_at: string;
  updated_at: string;
};

export type AgencyMemberOut = {
  id: string;
  agency_id: string;
  user_id: string;
  role: "owner" | "manager" | "member";
  status: "active" | "inactive";
  created_at: string;
  updated_at: string;
};

export type AgencyClientAccessOut = {
  id: string;
  agency_id: string;
  client_id: string;
  created_at: string;
  updated_at: string;
};

export type AgencyInviteOut = {
  id: string;
  agency_id: string;
  email: string;
  member_role: "owner" | "manager" | "member";
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  invited_by?: string | null;
  accepted_user_id?: string | null;
  accepted_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type AgencyInviteIssueResponse = {
  invite: AgencyInviteOut;
  invite_token: string;
  accept_url: string;
};

export type OperationalInsight = {
  scope: "account" | "client" | "agency";
  scope_id: string;
  title: string;
  reason: string;
  action: "scale" | "cap" | "pause" | "review";
  priority: "high" | "medium" | "low";
  score: number;
  metrics: Record<string, unknown>;
};

export type OperationalAction = {
  id: string;
  action: string;
  scope: string;
  scope_id: string;
  status: string;
  title: string;
  client_id?: string | null;
  account_id?: string | null;
  created_at: string;
};

export type AgencyOverview = {
  totals?: { spend: number };
  per_client: Array<{ client_id: string; spend: number }>;
  per_account?: Array<{ account_id: string; client_id: string; spend: number }>;
};

export type ClientOpsRow = {
  id: string;
  name: string;
  spend: number;
  budget: number;
  usage: number | null;
  pace: "critical" | "warning" | "stable" | "no_budget";
  riskScore: number;
  hasAlerts: boolean;
  owner: string;
  lastAction: OperationalAction | null;
};

export type TimelinePoint = { date: string; label: string; expected: number; actual: number | null };
export type TimelineAction = { date: string; action: string; title?: string };

export type ExternalAccountConfig = {
  id: string;
  platform: string;
  external_id: string;
  client_id?: string | null;
  name?: string | null;
  currency?: string | null;
};

export type ExternalInsightsSummary = {
  spend?: number;
  impressions?: number;
  clicks?: number;
  ctr?: number;
  cpc?: number;
  cpm?: number;
  conversions?: number;
  reach?: number;
  currency?: string;
};

export type MetaInsightsData = {
  summary: ExternalInsightsSummary;
  campaigns: Record<string, unknown>[];
  status?: string | null;
};

export type GoogleInsightsData = {
  summary: ExternalInsightsSummary;
  campaigns: Record<string, unknown>[];
  status?: string | null;
};

export type TikTokInsightsData = {
  summary: ExternalInsightsSummary;
  campaigns: Record<string, unknown>[];
  adgroups: Record<string, unknown>[];
  ads: Record<string, unknown>[];
  status?: string | null;
};
