from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AccountConfig(BaseModel):
    id: str
    platform: str
    external_id: str
    client_id: Optional[str] = None
    name: Optional[str] = None
    currency: Optional[str] = None


class MetaInsightsResponse(BaseModel):
    summary: Dict[str, object]
    campaigns: List[Dict[str, object]]
    status: Optional[str] = None


class GoogleInsightsResponse(BaseModel):
    summary: Dict[str, object]
    campaigns: List[Dict[str, object]]
    status: Optional[str] = None


class TikTokInsightsResponse(BaseModel):
    summary: Dict[str, object]
    campaigns: List[Dict[str, object]]
    adgroups: List[Dict[str, object]]
    ads: List[Dict[str, object]]
    status: Optional[str] = None


class BudgetBase(BaseModel):
    client_id: UUID
    scope: Literal["client", "account"] = Field(
        ...,
        description="Budget scope. `client` means client-level budget, `account` means account-level budget.",
    )
    account_id: Optional[UUID] = Field(
        None,
        description="Required when scope='account'. Must be null when scope='client'.",
    )
    amount: Decimal = Field(..., decimal_places=2, max_digits=14)
    currency: str = "USD"
    period_type: Literal["monthly", "custom"]
    start_date: date
    end_date: date
    note: Optional[str] = None
    created_by: Optional[UUID] = None

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope == "client" and self.account_id is not None:
            raise ValueError("scope='client' requires account_id=null")
        if self.scope == "account" and self.account_id is None:
            raise ValueError("scope='account' requires account_id")
        return self


class BudgetCreate(BudgetBase):
    pass


class BudgetPatch(BaseModel):
    client_id: Optional[UUID] = None
    scope: Optional[Literal["client", "account"]] = Field(
        None,
        description="If set to 'account' account_id is required; if 'client' account_id must be null.",
    )
    account_id: Optional[UUID] = Field(None, description="Target account for account-scoped budget.")
    amount: Optional[Decimal] = Field(None, decimal_places=2, max_digits=14)
    currency: Optional[str] = None
    period_type: Optional[Literal["monthly", "custom"]] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    note: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None
    changed_by: Optional[UUID] = Field(None, description="Actor id for audit history row.")


class BudgetOut(BaseModel):
    id: UUID
    client_id: UUID
    scope: Literal["client", "account"]
    account_id: Optional[UUID] = None
    amount: Decimal
    currency: str
    period_type: Literal["monthly", "custom"]
    start_date: date
    end_date: date
    status: Literal["active", "archived"]
    version: int
    note: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class BudgetHistoryOut(BaseModel):
    id: int
    budget_id: UUID
    changed_at: datetime
    changed_by: Optional[UUID] = None
    previous_values: Dict[str, object]
    new_values: Dict[str, object]


class BudgetTransferRequest(BaseModel):
    target_account_id: UUID
    amount: Decimal = Field(..., decimal_places=2, max_digits=14, gt=0)
    note: Optional[str] = None
    changed_by: Optional[UUID] = Field(None, description="Actor id for audit history rows.")


class BudgetTransferResponse(BaseModel):
    source_budget: BudgetOut
    target_budget: BudgetOut
    transferred_amount: Decimal


class BudgetTransferOut(BaseModel):
    id: int
    source_budget_id: UUID
    target_budget_id: UUID
    amount: Decimal
    note: Optional[str] = None
    changed_by: Optional[UUID] = None
    created_at: datetime


class ClientCreate(BaseModel):
    name: str = Field(..., min_length=1)
    legal_name: Optional[str] = None
    status: Literal["active", "inactive", "archived"] = "active"
    default_currency: str = "USD"
    timezone: Optional[str] = None
    notes: Optional[str] = None


class ClientPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    legal_name: Optional[str] = None
    status: Optional[Literal["active", "inactive", "archived"]] = None
    default_currency: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None


class ClientOut(BaseModel):
    id: UUID
    name: str
    legal_name: Optional[str] = None
    status: Literal["active", "inactive", "archived"]
    default_currency: str
    timezone: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AdAccountCreate(BaseModel):
    client_id: UUID
    platform: str
    external_account_id: str
    name: str
    currency: str = "USD"
    timezone: Optional[str] = None
    status: Literal["active", "inactive", "archived"] = "active"
    metadata: Optional[Dict[str, Any]] = None


class AdAccountPatch(BaseModel):
    client_id: Optional[UUID] = None
    platform: Optional[str] = None
    external_account_id: Optional[str] = None
    name: Optional[str] = None
    currency: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[Literal["active", "inactive", "archived"]] = None
    metadata: Optional[Dict[str, Any]] = None


class AdAccountOut(BaseModel):
    id: UUID
    client_id: UUID
    platform: str
    external_account_id: str
    name: str
    currency: str
    timezone: Optional[str] = None
    status: Literal["active", "inactive", "archived"]
    metadata: Optional[Dict[str, Any]] = None
    last_sync_at: Optional[datetime] = None
    sync_status: Optional[Literal["success", "error"]] = None
    sync_error: Optional[str] = None
    sync_error_code: Optional[str] = None
    sync_error_category: Optional[str] = None
    sync_retryable: Optional[bool] = None
    sync_next_retry_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AdAccountSyncRunRequest(BaseModel):
    account_ids: Optional[List[UUID]] = None
    platform: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    force: bool = False


class AdAccountDiscoverRequest(BaseModel):
    provider: Optional[str] = Field(
        default=None,
        description="Optional provider filter: meta|google|tiktok|all (facebook maps to meta).",
    )
    client_id: Optional[UUID] = Field(
        default=None,
        description="Target internal client for imported ad accounts. Required when multiple clients are in scope.",
    )
    upsert_existing: bool = Field(
        default=True,
        description="When true, updates existing accounts' name/currency/metadata and re-activates archived rows.",
    )


class AdAccountDiscoverResponse(BaseModel):
    requested_provider: str
    client_id: UUID
    discovered: int
    created: int
    updated: int
    skipped: int
    providers_attempted: List[str]
    providers_failed: Dict[str, str] = Field(default_factory=dict)
    items: List[AdAccountOut]


class AdAccountSyncJobOut(BaseModel):
    id: UUID
    ad_account_id: UUID
    provider: str
    status: Literal["success", "error"]
    started_at: datetime
    finished_at: Optional[datetime] = None
    records_synced: int = 0
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    error_category: Optional[str] = None
    retryable: bool = False
    attempt: int = 1
    next_retry_at: Optional[datetime] = None
    request_meta: Optional[Dict[str, Any]] = None
    created_by: Optional[UUID] = None
    created_at: datetime


class AdAccountSyncRunResponse(BaseModel):
    requested: int
    processed: int
    skipped: int = 0
    success: int
    failed: int
    retry_scheduled: int = 0
    started_at: datetime
    finished_at: datetime
    jobs: List[AdAccountSyncJobOut]


class IntegrationProviderOut(BaseModel):
    provider: str
    status: Literal["healthy", "warning", "error", "disconnected"]
    status_reason: Optional[str] = None
    auth_state: Literal["configured", "missing", "disabled"]
    token_hint: Optional[str] = None
    connection_sources: List[str] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)
    identity_linked_users: int = 0
    sync_ready: bool = False
    sync_readiness_reason: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    linked_accounts_count: int = 0
    affected_clients_count: int = 0
    last_heartbeat_at: Optional[datetime] = None
    last_successful_sync_at: Optional[datetime] = None
    last_error_time: Optional[datetime] = None
    last_error_safe: Optional[str] = None
    reconnect_available: bool = True


class IntegrationEventOut(BaseModel):
    provider: str
    level: Literal["success", "warning", "error"]
    title: str
    message: str
    occurred_at: datetime
    sync_job_id: Optional[UUID] = None


class IntegrationsOverviewResponse(BaseModel):
    summary: Dict[str, object]
    providers: List[IntegrationProviderOut]
    events: List[IntegrationEventOut]


class AdStatWrite(BaseModel):
    ad_account_id: UUID
    date: date
    platform: str
    impressions: int = 0
    clicks: int = 0
    spend: Decimal = Field(..., decimal_places=2, max_digits=14)
    conversions: Optional[Decimal] = Field(None, decimal_places=2, max_digits=14)


class AdStatsIngestRequest(BaseModel):
    rows: List[AdStatWrite]


class AdStatsIngestResponse(BaseModel):
    inserted: int
    updated: int
    total: int
    idempotency: Optional[Dict[str, object]] = None


class AdStatOut(BaseModel):
    id: UUID
    ad_account_id: UUID
    date: date
    platform: str
    impressions: int
    clicks: int
    spend: Decimal
    conversions: Optional[Decimal] = None
    created_at: datetime
    updated_at: datetime


class SpendAggregateOut(BaseModel):
    spend: Decimal
    impressions: int
    clicks: int
    conversions: Decimal
    ctr: Decimal
    cpc: Decimal
    cpm: Decimal


class OverviewResponse(BaseModel):
    range: Dict[str, object]
    scope: Dict[str, Optional[str]]
    spend_summary: Dict[str, object]
    budget_summary: Dict[str, object]
    breakdowns: Dict[str, object]


class AgencyOverviewResponse(BaseModel):
    range: Dict[str, object]
    totals: Dict[str, object]
    per_platform: List[Dict[str, object]]
    per_client: List[Dict[str, object]]
    per_account: List[Dict[str, object]]


class OperationalInsightOut(BaseModel):
    scope: Literal["account", "client", "agency"] = "account"
    scope_id: str
    title: str
    reason: str
    action: Literal["scale", "cap", "pause", "review"]
    priority: Literal["high", "medium", "low"]
    score: float
    metrics: Dict[str, object] = Field(default_factory=dict)


class OperationalInsightsResponse(BaseModel):
    range: Dict[str, object]
    scope: Dict[str, Optional[str]]
    items: List[OperationalInsightOut]


class OperationalActionExecuteRequest(BaseModel):
    action: Literal["scale", "cap", "pause", "review"]
    scope: Literal["account", "client", "agency"] = "account"
    scope_id: str
    title: str
    reason: str
    metrics: Dict[str, object] = Field(default_factory=dict)
    client_id: Optional[UUID] = None
    account_id: Optional[UUID] = None


class OperationalActionOut(BaseModel):
    id: UUID
    action: Literal["scale", "cap", "pause", "review"]
    scope: Literal["account", "client", "agency"]
    scope_id: str
    title: str
    reason: str
    metrics: Dict[str, object] = Field(default_factory=dict)
    client_id: Optional[UUID] = None
    account_id: Optional[UUID] = None
    status: Literal["queued", "applied", "failed"] = "queued"
    created_by: Optional[UUID] = None
    created_at: datetime


class UserCreate(BaseModel):
    email: Optional[str] = None
    name: str
    role: Literal["admin", "agency", "client"]
    status: Literal["active", "inactive"] = "active"


class UserOut(BaseModel):
    id: UUID
    email: Optional[str] = None
    name: str
    role: Literal["admin", "agency", "client"]
    status: Literal["active", "inactive"]
    created_at: datetime
    updated_at: datetime


class AuthIdentityLink(BaseModel):
    user_id: UUID
    provider: str
    provider_user_id: str
    email: Optional[str] = None
    email_verified: Optional[bool] = None
    raw_profile: Optional[Dict[str, Any]] = None


class AuthIdentityOut(BaseModel):
    id: UUID
    user_id: UUID
    provider: str
    provider_user_id: str
    email: Optional[str] = None
    email_verified: Optional[bool] = None
    raw_profile: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class UserClientAccessCreate(BaseModel):
    user_id: UUID
    client_id: UUID
    role: Literal["agency", "client"]


class UserClientAccessOut(BaseModel):
    id: UUID
    user_id: UUID
    client_id: UUID
    role: Literal["agency", "client"]
    created_at: datetime
    updated_at: datetime


class AgencyCreate(BaseModel):
    name: str = Field(..., min_length=1)
    slug: Optional[str] = None
    status: Literal["active", "suspended"] = "active"
    plan: str = "starter"
    notes: Optional[str] = None


class AgencyPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    slug: Optional[str] = None
    status: Optional[Literal["active", "suspended"]] = None
    plan: Optional[str] = None
    notes: Optional[str] = None


class AgencyOut(BaseModel):
    id: UUID
    name: str
    slug: str
    status: Literal["active", "suspended"]
    plan: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AgencyMemberCreate(BaseModel):
    user_id: UUID
    role: Literal["owner", "manager", "member"] = "member"
    status: Literal["active", "inactive"] = "active"


class AgencyMemberOut(BaseModel):
    id: UUID
    agency_id: UUID
    user_id: UUID
    role: Literal["owner", "manager", "member"]
    status: Literal["active", "inactive"]
    created_at: datetime
    updated_at: datetime


class AgencyClientAccessCreate(BaseModel):
    client_id: UUID


class AgencyClientAccessOut(BaseModel):
    id: UUID
    agency_id: UUID
    client_id: UUID
    created_at: datetime
    updated_at: datetime


class AgencyInviteCreate(BaseModel):
    email: str
    member_role: Literal["owner", "manager", "member"] = "member"
    expires_in_days: int = Field(7, ge=1, le=30)


class AgencyInviteResendRequest(BaseModel):
    expires_in_days: int = Field(7, ge=1, le=30)


class AgencyInviteOut(BaseModel):
    id: UUID
    agency_id: UUID
    email: str
    member_role: Literal["owner", "manager", "member"]
    status: Literal["pending", "accepted", "revoked", "expired"]
    expires_at: datetime
    invited_by: Optional[UUID] = None
    accepted_user_id: Optional[UUID] = None
    accepted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class SessionIssueRequest(BaseModel):
    user_id: UUID
    ttl_minutes: int = Field(60, ge=1, le=60 * 24 * 7)
    metadata: Optional[Dict[str, Any]] = None


class SessionIssueResponse(BaseModel):
    token: str
    session_id: UUID
    user_id: UUID
    expires_at: datetime


class AgencyInviteIssueResponse(BaseModel):
    invite: AgencyInviteOut
    invite_token: str
    accept_url: str


class AgencyInviteAcceptRequest(BaseModel):
    token: str
    name: Optional[str] = None


class AgencyInviteAcceptResponse(BaseModel):
    invite: AgencyInviteOut
    agency: AgencyOut
    member: AgencyMemberOut
    user: UserOut
    session: SessionIssueResponse


class SessionValidationResponse(BaseModel):
    valid: bool
    reason: Optional[str] = None
    session_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    user_role: Optional[Literal["admin", "agency", "client"]] = None
    expires_at: Optional[datetime] = None


class SessionValidateRequest(BaseModel):
    token: str


class AuthProviderConfigCreate(BaseModel):
    provider: str
    client_id: str
    client_secret: str
    redirect_uri: str
    enabled: bool = True


class AuthProviderConfigOut(BaseModel):
    id: UUID
    provider: str
    client_id: str
    client_secret: str
    redirect_uri: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ExternalIdentityResolveRequest(BaseModel):
    provider: str
    provider_user_id: str
    email: Optional[str] = None
    email_verified: Optional[bool] = None
    name: Optional[str] = None
    raw_profile: Optional[Dict[str, Any]] = None
    default_role: Literal["admin", "agency", "client"] = "client"
    issue_session: bool = True
    session_ttl_minutes: int = Field(60, ge=1, le=60 * 24 * 7)
    allow_email_merge: bool = False


class ExternalIdentityResolveResponse(BaseModel):
    user: UserOut
    identity: AuthIdentityOut
    session: Optional[SessionIssueResponse] = None


class SessionContextResponse(BaseModel):
    valid: bool
    reason: Optional[str] = None
    session_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    role: Optional[Literal["admin", "agency", "client"]] = None
    global_access: bool = False
    access_scope: Optional[Literal["all", "assigned"]] = None
    accessible_client_ids: List[UUID] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class AuthMeResponse(BaseModel):
    user: UserOut
    session: SessionContextResponse


class AuditLogOut(BaseModel):
    id: int
    event_type: str
    resource_type: str
    resource_id: Optional[str] = None
    actor_user_id: Optional[UUID] = None
    actor_role: Optional[str] = None
    tenant_client_id: Optional[UUID] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
