export type Client = {
  id: string;
  name: string;
  default_currency?: string;
};
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
  data_quality?: {
    status: "insufficient_data" | "partial" | "stale" | "fresh";
    rows_present: boolean;
    row_count: number;
    latest_data_date: string | null;
    stale_days: number | null;
    stale_after_days: number;
    active_accounts_count: number;
    accounts_with_data_count: number;
    accounts_without_data_count: number;
    coverage_percent: number;
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

export type AssignmentConflictLatestStat = {
  date: string;
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
};

export type AssignmentConflictCandidate = {
  account_id: string;
  client_id: string;
  client_name: string;
  client_status: "active" | "inactive" | "archived";
  account_name: string;
  account_status: "active" | "inactive" | "archived";
  platform: string;
  external_account_id: string;
  currency: string;
  latest_stat?: AssignmentConflictLatestStat | null;
  active_budget_count: number;
};

export type AssignmentConflictGroup = {
  group_id: string;
  group_version: string;
  platform: string;
  canonical_external_account_id: string;
  account_ids: string[];
  candidates: AssignmentConflictCandidate[];
  summary: {
    candidate_count: number;
    active_candidate_count: number;
    client_count: number;
    active_budget_count: number;
    latest_stat_date?: string | null;
  };
};

export type AssignmentConflictListResponse = {
  items: AssignmentConflictGroup[];
  count: number;
  summary: {
    conflict_groups: number;
    conflicted_accounts: number;
    active_budgets: number;
  };
};

export type AssignmentConflictResolveResponse = {
  status: "resolved";
  group_id: string;
  winner_account_id: string;
  loser_account_ids: string[];
  archived_budget_ids: string[];
  before: AssignmentConflictGroup;
  after: AssignmentConflictGroup;
  sync_required: boolean;
  resolved_at: string;
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
  sync_state: "healthy" | "no_data" | "error" | "retry_scheduled" | "never_synced";
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
    no_data: number;
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
  active_accounts_count?: number;
  successfully_synced_accounts_count?: number;
  accounts_with_data_count?: number;
  error_accounts_count?: number;
  never_synced_accounts_count?: number;
  stale_accounts_count?: number;
  assignment_conflict_accounts_count?: number;
  coverage_percent?: number;
  rows_present?: boolean;
  latest_data_date?: string | null;
  stale_days?: number | null;
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
    assignment_conflict_accounts?: number;
    total_errors_24h: number;
  };
  providers: IntegrationProvider[];
  events: IntegrationEvent[];
};

export type IntegrationConnection = {
  id: string;
  provider: string;
  scope_type: "global" | "agency" | "client";
  scope_id?: string | null;
  connection_key: string;
  status: "active" | "archived";
  created_by?: string | null;
  connected_account_label?: string | null;
  created_at: string;
  updated_at: string;
  credential_keys: string[];
  credentials_preview: Record<string, unknown>;
};

export type AlertOut = {
  id: string;
  code: string;
  severity: "critical" | "high" | "medium" | "low";
  status: "open" | "acked" | "resolved";
  title: string;
  message: string;
  fingerprint: string;
  provider?: string | null;
  client_id?: string | null;
  ad_account_id?: string | null;
  context: Record<string, unknown>;
  occurrences: number;
  first_seen_at: string;
  last_seen_at: string;
  acknowledged_at?: string | null;
  acknowledged_by?: string | null;
  resolved_at?: string | null;
};

export type SessionContext = {
  valid: boolean;
  reason?: string | null;
  session_id?: string | null;
  user_id?: string | null;
  role?: "admin" | "agency" | "client" | "solo_client" | null;
  global_access: boolean;
  access_scope?: "all" | "assigned" | null;
  accessible_client_ids: string[];
  expires_at?: string | null;
};

export type AuthUser = {
  id: string;
  email?: string | null;
  name: string;
  role: "admin" | "agency" | "client" | "solo_client";
  status: "active" | "inactive";
  created_at?: string;
  updated_at?: string;
};

export type AuthMeResponse = {
  user: AuthUser;
  session: SessionContext;
};

export type UserClientAccessOut = {
  id: string;
  user_id: string;
  client_id: string;
  role: "agency" | "client";
  created_at: string;
  updated_at: string;
};

export type AuditLogOut = {
  id: number;
  event_type: string;
  resource_type: string;
  resource_id?: string | null;
  actor_user_id?: string | null;
  actor_role?: string | null;
  tenant_client_id?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type AgencyOut = {
  id: string;
  name: string;
  slug: string;
  status: "active" | "suspended";
  plan: string;
  notes?: string | null;
  allow_client_invites: boolean;
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
  currency: string;
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
