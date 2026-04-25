from __future__ import annotations

import secrets
import threading
import time
import os
from datetime import date, datetime, timedelta
from typing import List, Optional, Union
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response

from app.schemas import (
    AdAccountCreate,
    AdAccountDiscoverRequest,
    AdAccountDiscoverResponse,
    AdAccountOut,
    AdAccountPatch,
    AdAccountSyncJobOut,
    AdAccountSyncDiagnosticOut,
    AdAccountSyncDiagnosticsResponse,
    AdAccountSyncRunRequest,
    AdAccountSyncRunResponse,
    AdStatOut,
    AdStatsIngestRequest,
    AdStatsIngestResponse,
    AgencyOverviewResponse,
    AgencyClientAccessCreate,
    AgencyClientAccessOut,
    AgencyCreate,
    AgencyInviteAcceptRequest,
    AgencyInviteAcceptResponse,
    AgencyInviteCreate,
    AgencyInviteIssueResponse,
    AgencyInviteOut,
    AgencyInviteResendRequest,
    AgencyMemberCreate,
    AgencyMemberOut,
    AgencyOut,
    AgencyPatch,
    OperationalInsightsResponse,
    OperationalActionExecuteRequest,
    OperationalActionOut,
    BudgetCreate,
    BudgetHistoryOut,
    BudgetOut,
    BudgetPatch,
    BudgetTransferOut,
    BudgetTransferRequest,
    BudgetTransferResponse,
    ClientCreate,
    ClientInviteAcceptResponse,
    ClientInviteCreate,
    ClientInviteIssueResponse,
    ClientInviteOut,
    ClientInviteResendRequest,
    ClientOut,
    ClientPatch,
    AuthIdentityLink,
    AuthIdentityOut,
    AuthProviderConfigCreate,
    AuthProviderConfigOut,
    AuthProviderConfigPublicOut,
    AuditLogOut,
    AlertOut,
    ExternalIdentityResolveRequest,
    ExternalIdentityResolveResponse,
    SessionIssueRequest,
    SessionIssueResponse,
    AuthMeResponse,
    AuthPasswordLoginRequest,
    SessionContextResponse,
    SessionValidateRequest,
    SessionValidationResponse,
    UserClientAccessCreate,
    UserClientAccessOut,
    UserCreate,
    UserPatch,
    UserOut,
    GoogleInsightsResponse,
    MetaInsightsResponse,
    IntegrationsOverviewResponse,
    IntegrationCredentialCreate,
    IntegrationCredentialOut,
    IntegrationCredentialPublicOut,
    IntegrationCredentialPatch,
    OverviewResponse,
    TikTokInsightsResponse,
)
from app.services.ad_accounts import AdAccountStore, InMemoryAdAccountStore, SqliteAdAccountStore
from app.services.ad_account_sync import (
    AdAccountSyncService,
    InMemoryAdAccountSyncJobStore,
    SqliteAdAccountSyncJobStore,
)
from app.services.ad_account_discovery import AdAccountDiscoveryService
from app.services.ad_stats import AdStatsStore, InMemoryAdStatsStore, SqliteAdStatsStore
from app.services.auth_arch import (
    ROLE_ACCESS_MODEL,
    AuthStore,
    InMemoryAuthStore,
    SqliteAuthStore,
)
from app.services.auth_facade import AuthFacadeService
from app.services.budgets import BudgetStore, InMemoryBudgetStore, SqliteBudgetStore
from app.services.clients import ClientStore, InMemoryClientStore, SqliteClientStore
from app.services.insights import get_google_insights, get_meta_insights, get_tiktok_insights
from app.services.integrations import build_integrations_overview
from app.services.integration_credentials import (
    InMemoryIntegrationCredentialStore,
    IntegrationCredentialStore,
    SqliteIntegrationCredentialStore,
)
from app.services.overview import OverviewService
from app.services.operational_insights import OperationalInsightsService
from app.services.operational_actions import (
    InMemoryOperationalActionStore,
    OperationalActionStore,
    SqliteOperationalActionStore,
)
from app.services.acl import RequestContext, ensure_account_access, ensure_admin, ensure_client_access
from app.services.providers import google_ads
from app.services.audit_log import AuditLogStore, InMemoryAuditLogStore, SqliteAuditLogStore
from app.services.alerts import AlertSignal, AlertStore, InMemoryAlertStore, SqliteAlertStore
from app.services.platform_admin import (
    InMemoryPlatformAdminStore,
    PlatformAdminStore,
    SqlitePlatformAdminStore,
)
from app.services.rate_limit import InMemoryRateLimiter
from app.services.oauth import (
    FacebookOAuthAdapter,
    GoogleOAuthAdapter,
    InMemoryOAuthStateStore,
    OAuthProviderAdapter,
    OAuthProviderConfig,
    OAuthStateStore,
    SqliteOAuthStateStore,
)
from app.settings import get_settings, load_accounts
from app.db import sqlite_conn


settings = get_settings()
app = FastAPI(
    title="Envidicy Digital Dashboard Backend",
    version="0.3.0",
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    # Cookie-based auth needs credentials; '*' cannot be used with credentials.
    allow_origins=[
        o
        for o in settings.allowed_origins
        if o != "*"
    ]
    or ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Default production stores (sqlite runtime)
client_store: ClientStore = SqliteClientStore(settings.budgets_db_path)
ad_account_store: AdAccountStore = SqliteAdAccountStore(settings.budgets_db_path, client_store)
integration_credential_store: IntegrationCredentialStore = SqliteIntegrationCredentialStore(settings.budgets_db_path)
ad_account_sync_job_store = SqliteAdAccountSyncJobStore(settings.budgets_db_path)
ad_stats_store: AdStatsStore = SqliteAdStatsStore(settings.budgets_db_path, ad_account_store)


def _resolve_provider_credentials(
    provider: str,
    client_id: UUID,
    user_id: Optional[UUID],
) -> Optional[dict]:
    rows = integration_credential_store.resolve_many_for_client(provider=provider, client_id=client_id, user_id=user_id)
    if not rows:
        return None
    return dict(rows[0].credentials)


def _resolve_provider_credentials_candidates(
    provider: str,
    client_id: UUID,
    user_id: Optional[UUID],
) -> List[dict]:
    rows = integration_credential_store.resolve_many_for_client(provider=provider, client_id=client_id, user_id=user_id)
    out: List[dict] = []
    for row in rows:
        cred = dict(row.credentials)
        cred["__credential_id"] = str(row.id)
        cred["__connection_key"] = row.connection_key
        out.append(cred)
    return out


ad_account_sync_service = AdAccountSyncService(
    account_store=ad_account_store,
    job_store=ad_account_sync_job_store,
    ad_stats_store=ad_stats_store,
    credential_resolver=_resolve_provider_credentials,
    credential_candidates_resolver=_resolve_provider_credentials_candidates,
)
ad_account_discovery_service = AdAccountDiscoveryService(
    account_store=ad_account_store,
    credential_resolver=_resolve_provider_credentials,
    credential_candidates_resolver=_resolve_provider_credentials_candidates,
)
budget_store: BudgetStore = SqliteBudgetStore(settings.budgets_db_path)
auth_store: AuthStore = SqliteAuthStore(settings.budgets_db_path)
platform_admin_store: PlatformAdminStore = SqlitePlatformAdminStore(settings.budgets_db_path, auth_store)
oauth_state_store: OAuthStateStore = SqliteOAuthStateStore(settings.budgets_db_path)
oauth_adapters: dict[str, OAuthProviderAdapter] = {
    "facebook": FacebookOAuthAdapter(),
    "google": GoogleOAuthAdapter(),
}
auth_facade = AuthFacadeService(auth_store=auth_store)

app.state.client_store = client_store
app.state.ad_account_store = ad_account_store
app.state.integration_credential_store = integration_credential_store
app.state.ad_account_sync_service = ad_account_sync_service
app.state.ad_account_discovery_service = ad_account_discovery_service
app.state.ad_stats_store = ad_stats_store
app.state.budget_store = budget_store
app.state.auth_store = auth_store
app.state.platform_admin_store = platform_admin_store
app.state.oauth_state_store = oauth_state_store
app.state.oauth_adapters = oauth_adapters
app.state.auth_facade = auth_facade
app.state.overview_service = OverviewService(ad_stats_store=ad_stats_store, ad_account_store=ad_account_store, budget_store=budget_store)
app.state.operational_insights_service = OperationalInsightsService(rules=settings.operational_insights_rules)
app.state.operational_action_store = SqliteOperationalActionStore(settings.budgets_db_path)
app.state.audit_log_store = SqliteAuditLogStore(settings.budgets_db_path)
app.state.alert_store = SqliteAlertStore(settings.budgets_db_path)
app.state.rate_limiter = InMemoryRateLimiter()


class RuntimeMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.requests_total: dict[tuple[str, str, int], int] = {}
        self.request_duration_seconds_sum: dict[tuple[str, str], float] = {}
        self.request_duration_seconds_count: dict[tuple[str, str], int] = {}

    def record(self, *, method: str, path: str, status_code: int, duration_seconds: float) -> None:
        with self._lock:
            key = (method, path, status_code)
            self.requests_total[key] = self.requests_total.get(key, 0) + 1
            d_key = (method, path)
            self.request_duration_seconds_sum[d_key] = (
                self.request_duration_seconds_sum.get(d_key, 0.0) + max(0.0, duration_seconds)
            )
            self.request_duration_seconds_count[d_key] = self.request_duration_seconds_count.get(d_key, 0) + 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "started_at": self.started_at,
                "requests_total": dict(self.requests_total),
                "request_duration_seconds_sum": dict(self.request_duration_seconds_sum),
                "request_duration_seconds_count": dict(self.request_duration_seconds_count),
            }


app.state.runtime_metrics = RuntimeMetrics()

def _origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    norm = origin.strip().rstrip("/")
    return norm in settings.allowed_origins


def _attach_cors_headers(request: Request, response: Response) -> Response:
    origin = (request.headers.get("origin") or "").strip()
    if not origin or not _origin_allowed(origin):
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = "authorization,content-type,x-csrf-token"
    response.headers["Access-Control-Allow-Methods"] = "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"
    response.headers["Vary"] = "Origin"
    return response


def _attach_security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    if settings.app_env.lower() in {"prod", "production"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _mask_secret_value(value: object) -> object:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "********"
    return f"{'*' * (len(text) - 4)}{text[-4:]}"


def _is_sensitive_credential_key(key: str) -> bool:
    lowered = (key or "").lower()
    tokens = ("token", "secret", "password", "api_key", "key", "refresh")
    return any(t in lowered for t in tokens)


def _to_public_provider_config(cfg: AuthProviderConfigOut) -> AuthProviderConfigPublicOut:
    return AuthProviderConfigPublicOut(
        id=cfg.id,
        provider=cfg.provider,
        client_id=cfg.client_id,
        redirect_uri=cfg.redirect_uri,
        enabled=cfg.enabled,
        client_secret_configured=bool(str(cfg.client_secret or "").strip()),
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


def _to_public_integration_credential(row: IntegrationCredentialOut) -> IntegrationCredentialPublicOut:
    preview: dict = {}
    keys = sorted(row.credentials.keys())
    for k in keys:
        v = row.credentials.get(k)
        if _is_sensitive_credential_key(k):
            preview[k] = _mask_secret_value(v)
        else:
            preview[k] = v
    return IntegrationCredentialPublicOut(
        id=row.id,
        provider=row.provider,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        connection_key=row.connection_key,
        status=row.status,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        credential_keys=keys,
        credentials_preview=preview,
    )


def _visible_integration_credentials_for_ctx(
    ctx: RequestContext,
    *,
    status: str = "active",
    provider: Optional[str] = None,
) -> List[IntegrationCredentialOut]:
    rows = _integration_credential_store().list(status=status, provider=provider)
    if ctx.role == "admin":
        return rows
    if ctx.role != "agency":
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Agency/admin access required"})
    agency_ids = {str(x) for x in _agency_scope_ids_for_user(ctx.user_id)}
    return [
        x
        for x in rows
        if x.scope_type == "agency" and x.scope_id and str(x.scope_id) in agency_ids
    ]


def _ensure_credential_manage_access(ctx: RequestContext, row: IntegrationCredentialOut) -> None:
    if ctx.role == "admin":
        return
    if ctx.role != "agency":
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Agency/admin access required"})
    agency_ids = {str(x) for x in _agency_scope_ids_for_user(ctx.user_id)}
    if row.scope_type != "agency" or not row.scope_id or str(row.scope_id) not in agency_ids:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Credential scope access denied"})


def _ensure_alert_access(ctx: RequestContext, row: AlertOut) -> None:
    if ctx.role == "admin":
        return
    if row.client_id and row.client_id in ctx.accessible_client_ids:
        return
    raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Alert tenant access denied"})


def _ensure_agency_member_access(ctx: RequestContext, agency_id: UUID, *, manage: bool = False) -> None:
    if ctx.role == "admin":
        return
    if ctx.role != "agency" or not ctx.user_id:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Agency/admin access required"})
    try:
        members = _platform_admin_store().list_members(agency_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Agency not found")
    me = next((m for m in members if m.user_id == ctx.user_id and m.status == "active"), None)
    if not me:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Agency access denied"})
    if manage and me.role not in {"owner", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Agency management access denied"})


def _status_code_to_error_code(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
    }
    return mapping.get(status_code, "application_error")


def _safe_sync_error_message(raw: Optional[str]) -> str:
    msg = str(raw or "").strip().lower()
    if not msg:
        return "No provider error details available."
    if "expired" in msg or "unauthorized" in msg or "invalid token" in msg:
        return "Authentication expired or invalid. Reconnect provider."
    if "scope" in msg or "permission" in msg or "forbidden" in msg or "access" in msg:
        return "Insufficient permissions for required API scopes."
    if "rate" in msg or "throttl" in msg or "quota" in msg:
        return "Provider is rate-limiting requests. Retry later."
    if "not set" in msg or "credentials" in msg or "credential" in msg:
        return "Provider credentials are missing or incomplete."
    if "customer_not_enabled" in msg or ("customer" in msg and "not enabled" in msg):
        return "Account is not enabled in provider and cannot be synced."
    if "user_permission_denied" in msg or "login-customer-id" in msg:
        return "Account is outside current manager hierarchy. Check MCC/login customer scope."
    return "Sync failed. Check provider diagnostics and retry."


def _sync_action_hint(*, state: str, error_code: Optional[str], retryable: bool) -> str:
    if state == "healthy":
        return "No action needed."
    if state == "never_synced":
        return "Run initial sync for this account."
    if state == "retry_scheduled":
        return "Retry is already scheduled automatically. You can force sync if needed."
    if error_code == "auth_failed":
        return "Reconnect provider credentials or fix account permissions."
    if error_code == "invalid_request":
        return "Check account mapping and provider account configuration."
    if retryable:
        return "Retry later or run force sync after provider recovers."
    return "Inspect account/provider diagnostics and retry sync."


def _error_envelope(status_code: int, detail) -> dict:
    code = _status_code_to_error_code(status_code)
    message = "Request failed"
    payload_details = {}

    if isinstance(detail, dict):
        code = str(detail.get("code") or code)
        message = str(detail.get("message") or detail.get("detail") or message)
        payload_details = detail.get("details") if isinstance(detail.get("details"), dict) else {
            k: v for k, v in detail.items() if k not in {"code", "message", "detail"}
        }
    elif isinstance(detail, str):
        message = detail
    elif detail is not None:
        message = str(detail)

    return {"error": {"code": code, "message": message, "details": payload_details}}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content=_error_envelope(exc.status_code, exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Validation failed",
                "details": {"errors": exc.errors()},
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Internal server error",
                "details": {"exception_type": type(exc).__name__},
            }
        },
    )


@app.middleware("http")
async def auth_security_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    ip = request.client.host if request.client else "unknown"
    request_id = (request.headers.get("X-Request-Id") or str(uuid4())).strip()
    started = time.monotonic()

    def finalize(resp: Response) -> Response:
        resp = _attach_cors_headers(request, resp)
        resp = _attach_security_headers(resp)
        resp.headers["X-Request-Id"] = request_id
        route = request.scope.get("route")
        route_path = getattr(route, "path", path) if route else path
        duration = time.monotonic() - started
        if settings.metrics_enabled:
            _runtime_metrics().record(method=method, path=route_path, status_code=resp.status_code, duration_seconds=duration)
        if settings.request_log_enabled:
            log_line = {
                "ts": datetime.utcnow().isoformat(),
                "request_id": request_id,
                "method": method,
                "path": route_path,
                "status_code": resp.status_code,
                "duration_ms": round(duration * 1000, 2),
                "client_ip": ip,
            }
            print(log_line)
        return resp

    # Handle CORS preflight early to avoid auth/rate-limit/CSRF interference.
    if method == "OPTIONS":
        return finalize(Response(status_code=204))

    if settings.auth_rate_limit_enabled:
        limit = None
        bucket = None
        if path.startswith("/auth/invites/accept"):
            limit = settings.auth_rate_limit_invite_max_requests
            bucket = "auth-invite"
        elif path.startswith("/auth/"):
            limit = settings.auth_rate_limit_auth_max_requests
            bucket = "auth"
        elif path.startswith("/platform/agencies/") and "/invites" in path:
            limit = settings.auth_rate_limit_admin_invite_max_requests
            bucket = "admin-invite"
        if limit is not None and bucket is not None:
            decision = _rate_limiter().allow(
                f"{bucket}:{ip}",
                max_requests=limit,
                window_seconds=settings.auth_rate_limit_window_seconds,
            )
            if not decision.allowed:
                return finalize(JSONResponse(
                    status_code=429,
                    content=_error_envelope(
                        429,
                        {
                            "code": "rate_limited",
                            "message": "Too many requests",
                            "details": {"retry_after_seconds": decision.retry_after_seconds},
                        },
                    ),
                    headers={"Retry-After": str(decision.retry_after_seconds)},
                ))

    if settings.csrf_enforce_cookie_auth and method in {"POST", "PATCH", "PUT", "DELETE"}:
        csrf_exempt = {
            "/auth/invites/accept",
            "/auth/password/login",
            "/auth/logout",
            "/auth/internal/sessions/issue",
            "/auth/internal/sessions/validate",
            "/auth/internal/sessions/revoke",
        }
        if settings.enable_test_endpoints:
            csrf_exempt.add("/_testing/use-inmemory-stores")
        if path not in csrf_exempt:
            has_bearer = (request.headers.get("Authorization") or "").lower().startswith("bearer ")
            has_x_token = bool(request.headers.get("X-Session-Token"))
            has_cookie_session = bool(request.cookies.get(settings.auth_cookie_name))
            if has_cookie_session and not has_bearer and not has_x_token:
                cookie_token = request.cookies.get(settings.csrf_cookie_name) or ""
                header_token = request.headers.get(settings.csrf_header_name) or ""
                if not cookie_token or cookie_token != header_token:
                    return finalize(JSONResponse(
                        status_code=403,
                        content=_error_envelope(
                            403,
                            {"code": "csrf_failed", "message": "CSRF validation failed", "details": {}},
                        ),
                    ))

    response = await call_next(request)
    return finalize(response)


@app.post("/_testing/use-inmemory-stores", include_in_schema=False)
def use_inmemory_stores():
    if not settings.enable_test_endpoints:
        raise HTTPException(status_code=404, detail="Not found")
    # Helper for tests to avoid file I/O and cross-test state leakage.
    c = InMemoryClientStore()
    a = InMemoryAdAccountStore(c)
    integration_creds = InMemoryIntegrationCredentialStore()
    def _resolve_provider_credentials_inmemory(provider: str, client_id: UUID, user_id: Optional[UUID]) -> Optional[dict]:
        rows = integration_creds.resolve_many_for_client(provider=provider, client_id=client_id, user_id=user_id)
        if not rows:
            return None
        return dict(rows[0].credentials)
    def _resolve_provider_credentials_candidates_inmemory(
        provider: str,
        client_id: UUID,
        user_id: Optional[UUID],
    ) -> List[dict]:
        rows = integration_creds.resolve_many_for_client(provider=provider, client_id=client_id, user_id=user_id)
        out: List[dict] = []
        for row in rows:
            cred = dict(row.credentials)
            cred["__credential_id"] = str(row.id)
            cred["__connection_key"] = row.connection_key
            out.append(cred)
        return out
    sync_jobs = InMemoryAdAccountSyncJobStore()
    s = InMemoryAdStatsStore(a)
    sync_service = AdAccountSyncService(
        account_store=a,
        job_store=sync_jobs,
        ad_stats_store=s,
        credential_resolver=_resolve_provider_credentials_inmemory,
        credential_candidates_resolver=_resolve_provider_credentials_candidates_inmemory,
    )
    discovery_service = AdAccountDiscoveryService(
        account_store=a,
        credential_resolver=_resolve_provider_credentials_inmemory,
        credential_candidates_resolver=_resolve_provider_credentials_candidates_inmemory,
    )
    b = InMemoryBudgetStore()
    auth = InMemoryAuthStore()
    platform_admin = InMemoryPlatformAdminStore(auth)
    oauth_states = InMemoryOAuthStateStore()
    app.state.client_store = c
    app.state.ad_account_store = a
    app.state.integration_credential_store = integration_creds
    app.state.ad_account_sync_service = sync_service
    app.state.ad_account_discovery_service = discovery_service
    app.state.ad_stats_store = s
    app.state.budget_store = b
    app.state.auth_store = auth
    app.state.platform_admin_store = platform_admin
    app.state.oauth_state_store = oauth_states
    app.state.oauth_adapters = {"facebook": FacebookOAuthAdapter(), "google": GoogleOAuthAdapter()}
    app.state.auth_facade = AuthFacadeService(auth_store=auth)
    app.state.overview_service = OverviewService(ad_stats_store=s, ad_account_store=a, budget_store=b)
    app.state.operational_insights_service = OperationalInsightsService(rules=settings.operational_insights_rules)
    app.state.operational_action_store = InMemoryOperationalActionStore()
    app.state.audit_log_store = InMemoryAuditLogStore()
    app.state.alert_store = InMemoryAlertStore()
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.runtime_metrics = RuntimeMetrics()
    return {"status": "ok"}


def _client_store() -> ClientStore:
    return app.state.client_store


def _ad_account_store() -> AdAccountStore:
    return app.state.ad_account_store


def _integration_credential_store() -> IntegrationCredentialStore:
    return app.state.integration_credential_store


def _ad_stats_store() -> AdStatsStore:
    return app.state.ad_stats_store


def _ad_account_sync_service() -> AdAccountSyncService:
    return app.state.ad_account_sync_service


def _ad_account_discovery_service() -> AdAccountDiscoveryService:
    return app.state.ad_account_discovery_service


def _budget_store() -> BudgetStore:
    return app.state.budget_store


def _auth_store() -> AuthStore:
    return app.state.auth_store


def _platform_admin_store() -> PlatformAdminStore:
    return app.state.platform_admin_store


def _oauth_state_store() -> OAuthStateStore:
    return app.state.oauth_state_store


def _oauth_adapters() -> dict[str, OAuthProviderAdapter]:
    return app.state.oauth_adapters


def _overview_service() -> OverviewService:
    return app.state.overview_service


def _auth_facade() -> AuthFacadeService:
    return app.state.auth_facade


def _operational_insights_service() -> OperationalInsightsService:
    return app.state.operational_insights_service


def _operational_action_store() -> OperationalActionStore:
    return app.state.operational_action_store


def _audit_log_store() -> AuditLogStore:
    return app.state.audit_log_store


def _alert_store() -> AlertStore:
    return app.state.alert_store


def _rate_limiter() -> InMemoryRateLimiter:
    return app.state.rate_limiter


def _runtime_metrics() -> RuntimeMetrics:
    return app.state.runtime_metrics


def _audit_event(
    *,
    event_type: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    ctx: Optional[RequestContext] = None,
    tenant_client_id: Optional[UUID] = None,
    payload: Optional[dict] = None,
) -> None:
    try:
        _audit_log_store().create(
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_user_id=ctx.user_id if ctx else None,
            actor_role=ctx.role if ctx else None,
            tenant_client_id=tenant_client_id,
            payload=payload or {},
        )
    except Exception:
        # Audit write failure must not break primary business flow.
        return


def _is_blocked_account_error(message: Optional[str]) -> bool:
    m = str(message or "").strip().lower()
    if not m:
        return False
    blocked_tokens = (
        "customer_not_enabled",
        "not enabled",
        "blocked",
        "disabled",
        "suspended",
    )
    return any(t in m for t in blocked_tokens)


def _build_sync_alert_signal(job: AdAccountSyncJobOut, *, client_id: UUID) -> Optional[AlertSignal]:
    if job.status != "error":
        return None
    provider = (job.provider or "").lower().strip() or None
    error_code = str(job.error_code or "").strip().lower()
    message = str(job.error_message or "").strip() or "Sync failed with unknown provider error"

    if _is_blocked_account_error(message):
        return AlertSignal(
            code="account.blocked_or_disabled",
            severity="critical",
            title="Ad account blocked/disabled",
            message="Provider reports the ad account is blocked, disabled, or not enabled.",
            fingerprint=f"account-blocked:{provider}:{job.ad_account_id}",
            provider=provider,
            client_id=client_id,
            ad_account_id=job.ad_account_id,
            context={"error_code": job.error_code, "raw_error": message},
        )

    if error_code == "auth_failed":
        return AlertSignal(
            code="provider.auth_failed",
            severity="high",
            title="Provider authorization failed",
            message="Provider authorization or permissions failed. Reconnect credentials.",
            fingerprint=f"provider-auth:{provider}:{job.ad_account_id}",
            provider=provider,
            client_id=client_id,
            ad_account_id=job.ad_account_id,
            context={"error_code": job.error_code, "raw_error": message},
        )

    if error_code in {"provider_unavailable", "rate_limited"}:
        return AlertSignal(
            code="provider.unavailable",
            severity="medium",
            title="Provider temporarily unavailable",
            message="Provider API is unavailable or throttling sync calls.",
            fingerprint=f"provider-unavailable:{provider}:{client_id}",
            provider=provider,
            client_id=client_id,
            ad_account_id=None,
            context={"error_code": job.error_code, "raw_error": message},
        )

    return None


def _process_sync_alerts(*, jobs: List[AdAccountSyncJobOut], account_client_by_id: dict[UUID, UUID]) -> None:
    for job in jobs:
        client_id = account_client_by_id.get(job.ad_account_id)
        if not client_id:
            continue
        provider = (job.provider or "").lower().strip()
        if job.status == "success":
            _alert_store().resolve_by_fingerprint(f"account-blocked:{provider}:{job.ad_account_id}")
            _alert_store().resolve_by_fingerprint(f"provider-auth:{provider}:{job.ad_account_id}")
            _alert_store().resolve_by_fingerprint(f"provider-unavailable:{provider}:{client_id}")
            continue
        signal = _build_sync_alert_signal(job, client_id=client_id)
        if signal:
            _alert_store().raise_alert(signal)


def _process_discovery_alerts(
    *,
    target_client_id: UUID,
    providers_attempted: List[str],
    providers_failed: dict[str, str],
) -> None:
    attempted = {(x or "").lower().strip() for x in providers_attempted if str(x or "").strip()}
    for provider, error in providers_failed.items():
        p = (provider or "").lower().strip()
        msg = str(error or "").strip()
        lowered = msg.lower()
        severity = "medium"
        code = "discovery.provider_failed"
        title = "Provider discovery failed"
        if _is_blocked_account_error(msg):
            code = "discovery.account_blocked_or_disabled"
            severity = "critical"
            title = "Provider reported blocked/disabled account during discovery"
        elif "auth" in lowered or "permission" in lowered or "forbidden" in lowered or "unauthorized" in lowered:
            code = "discovery.auth_failed"
            severity = "high"
            title = "Provider authorization failed during discovery"
        _alert_store().raise_alert(
            AlertSignal(
                code=code,
                severity=severity,
                title=title,
                message=msg or "Provider discovery failed",
                fingerprint=f"discovery-failed:{p}:{target_client_id}",
                provider=p or None,
                client_id=target_client_id,
                ad_account_id=None,
                context={"raw_error": msg},
            )
        )

    for provider in attempted:
        if provider and provider not in {str(k).lower().strip() for k in providers_failed.keys()}:
            _alert_store().resolve_by_fingerprint(f"discovery-failed:{provider}:{target_client_id}")


def _oauth_provider_config_or_400(provider: str) -> OAuthProviderConfig:
    cfg = next((x for x in _auth_store().list_provider_configs() if x.provider == provider), None)
    if not cfg or not cfg.enabled:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "provider_not_configured",
                "message": f"OAuth provider '{provider}' is not configured or disabled",
            },
        )
    return OAuthProviderConfig(
        provider=cfg.provider,
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        redirect_uri=cfg.redirect_uri,
        enabled=cfg.enabled,
    )


def _agency_scope_ids_for_user(user_id: UUID) -> List[UUID]:
    out: List[UUID] = []
    try:
        agencies = _platform_admin_store().list_agencies(status="active")
    except Exception:
        return out
    for agency in agencies:
        try:
            members = _platform_admin_store().list_members(agency.id)
        except Exception:
            continue
        if any(m.user_id == user_id and m.status == "active" for m in members):
            out.append(agency.id)
    return out


def _integration_credentials_from_oauth(
    provider: str,
    oauth_tokens: Optional[dict],
    cfg: OAuthProviderConfig,
) -> Optional[dict]:
    tokens = oauth_tokens or {}
    p = provider.strip().lower()
    if p in {"facebook", "meta"}:
        access_token = str(tokens.get("access_token") or "").strip()
        if not access_token:
            return None
        business_ids = [x.strip() for x in str(os.getenv("META_BUSINESS_IDS", "")).split(",") if x.strip()]
        return {"access_token": access_token, "business_ids": business_ids}

    if p == "google":
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        # Google Ads client requires refresh token for durable server-side sync.
        if not refresh_token:
            return None
        payload = {
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "refresh_token": refresh_token,
        }
        developer_token = str(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")).strip()
        if developer_token:
            payload["developer_token"] = developer_token
        # Detect tenant-specific login_customer_id from this OAuth credential set.
        # This avoids pinning agency sync to a global default MCC.
        payload["login_customer_id"] = google_ads.detect_login_customer_id(payload)
        return payload
    return None


def _auto_upsert_integration_credentials(
    *,
    provider: str,
    user: UserOut,
    provider_user_id: Optional[str],
    connect_mode: str = "add",
    requested_connection_key: Optional[str] = None,
    oauth_tokens: Optional[dict],
    cfg: OAuthProviderConfig,
) -> None:
    credentials = _integration_credentials_from_oauth(provider, oauth_tokens, cfg)
    if not credentials:
        return
    provider_norm = "meta" if provider.strip().lower() == "facebook" else provider.strip().lower()

    def _connection_key_for_credentials() -> str:
        explicit_key = str(requested_connection_key or "").strip()
        if connect_mode == "overwrite" and explicit_key:
            return explicit_key
        # Google users can connect multiple MCC trees; key credentials by login_customer_id when known.
        if provider_norm == "google":
            login_customer_id = str(credentials.get("login_customer_id") or "").strip()
            if login_customer_id:
                return f"google:{login_customer_id}"
        normalized_provider_user = str(provider_user_id or "").strip()
        if normalized_provider_user:
            return f"{provider_norm}:{normalized_provider_user}"
        return "default"

    if user.role == "agency":
        agency_ids = _agency_scope_ids_for_user(user.id)
        if not agency_ids:
            return
        connection_key = _connection_key_for_credentials()
        for agency_id in agency_ids:
            _integration_credential_store().upsert(
                IntegrationCredentialCreate(
                    provider=provider_norm,
                    scope_type="agency",
                    scope_id=agency_id,
                    connection_key=connection_key,
                    credentials=credentials,
                    created_by=user.id,
                )
            )
        return
    if user.role == "admin":
        connection_key = _connection_key_for_credentials()
        _integration_credential_store().upsert(
            IntegrationCredentialCreate(
                provider=provider_norm,
                scope_type="global",
                scope_id=None,
                connection_key=connection_key,
                credentials=credentials,
                created_by=user.id,
            )
        )


def _invite_token_hash(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _derive_name_from_email(email: str) -> str:
    local = (email or "").split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
    return local.title() or "Client User"


def _to_client_invite_out(row) -> ClientInviteOut:
    return ClientInviteOut(
        id=UUID(row["id"]),
        client_id=UUID(row["client_id"]),
        email=row["email"],
        status=row["status"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        invited_by=UUID(row["invited_by"]) if row["invited_by"] else None,
        accepted_user_id=UUID(row["accepted_user_id"]) if row["accepted_user_id"] else None,
        accepted_at=datetime.fromisoformat(row["accepted_at"]) if row["accepted_at"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _issue_client_invite(
    *,
    client_id: UUID,
    email: str,
    expires_in_days: int,
    invited_by: Optional[UUID],
) -> ClientInviteIssueResponse:
    now = datetime.utcnow()
    expires_at = now + timedelta(days=max(1, int(expires_in_days)))
    invite_id = str(uuid4())
    token = secrets.token_urlsafe(32)
    token_hash = _invite_token_hash(token)
    norm_email = email.strip().lower()
    if not norm_email:
        raise HTTPException(status_code=400, detail={"code": "invalid_email", "message": "email is required"})

    with sqlite_conn(settings.budgets_db_path) as conn:
        row_client = conn.execute("SELECT id FROM clients WHERE id=?", (str(client_id),)).fetchone()
        if not row_client:
            raise HTTPException(status_code=404, detail="Client not found")
        conn.execute(
            """
            UPDATE client_invites
            SET status='revoked', updated_at=?
            WHERE client_id=? AND lower(email)=lower(?) AND status='pending'
            """,
            (now.isoformat(), str(client_id), norm_email),
        )
        conn.execute(
            """
            INSERT INTO client_invites
            (id, client_id, email, token_hash, status, expires_at, invited_by, accepted_user_id, accepted_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, ?, ?)
            """,
            (
                invite_id,
                str(client_id),
                norm_email,
                token_hash,
                expires_at.isoformat(),
                str(invited_by) if invited_by else None,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()

    base = settings.frontend_base_url.rstrip("/")
    accept_url = f"{base}/login?invite_token={token}"
    return ClientInviteIssueResponse(invite=_to_client_invite_out(row), invite_token=token, accept_url=accept_url)


def _list_client_invites(*, client_id: UUID, status: str = "all") -> List[ClientInviteOut]:
    now_iso = datetime.utcnow().isoformat()
    with sqlite_conn(settings.budgets_db_path) as conn:
        conn.execute(
            """
            UPDATE client_invites
            SET status='expired', updated_at=?
            WHERE client_id=? AND status='pending' AND expires_at < ?
            """,
            (now_iso, str(client_id), now_iso),
        )
        conn.commit()
        where = "WHERE client_id=?"
        params: List[object] = [str(client_id)]
        if status != "all":
            where += " AND status=?"
            params.append(status)
        rows = conn.execute(
            f"SELECT * FROM client_invites {where} ORDER BY updated_at DESC",
            tuple(params),
        ).fetchall()
    return [_to_client_invite_out(r) for r in rows]


def _revoke_client_invite(*, client_id: UUID, invite_id: UUID) -> ClientInviteOut:
    now_iso = datetime.utcnow().isoformat()
    with sqlite_conn(settings.budgets_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM client_invites WHERE id=? AND client_id=?",
            (str(invite_id), str(client_id)),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Client invite not found")
        if row["status"] in {"accepted", "expired", "revoked"}:
            return _to_client_invite_out(row)
        conn.execute(
            "UPDATE client_invites SET status='revoked', updated_at=? WHERE id=?",
            (now_iso, str(invite_id)),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM client_invites WHERE id=?", (str(invite_id),)).fetchone()
    return _to_client_invite_out(updated)


def _accept_client_invite(payload: AgencyInviteAcceptRequest) -> ClientInviteAcceptResponse:
    now = datetime.utcnow()
    token_hash = _invite_token_hash(payload.token.strip())
    with sqlite_conn(settings.budgets_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM client_invites WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail={"code": "invalid_invite", "message": "Invalid invite token"})
        status = row["status"]
        expires_at = datetime.fromisoformat(row["expires_at"])
        if status == "accepted":
            raise HTTPException(status_code=400, detail={"code": "invite_used", "message": "Invite already accepted"})
        if status == "revoked":
            raise HTTPException(status_code=400, detail={"code": "invite_revoked", "message": "Invite was revoked"})
        if expires_at <= now:
            conn.execute("UPDATE client_invites SET status='expired', updated_at=? WHERE id=?", (now.isoformat(), row["id"]))
            conn.commit()
            raise HTTPException(status_code=400, detail={"code": "invite_expired", "message": "Invite expired"})
        client_row = conn.execute("SELECT * FROM clients WHERE id=?", (row["client_id"],)).fetchone()
        if not client_row:
            raise HTTPException(status_code=404, detail={"code": "client_not_found", "message": "Client not found"})

    email = str(row["email"]).strip().lower()
    existing_user = _auth_store().find_user_by_email(email)
    if existing_user:
        user = existing_user
        if user.status != "active":
            user = _auth_store().patch_user(user.id, UserPatch(status="active"))
        if payload.name and payload.name.strip():
            user = _auth_store().patch_user(user.id, UserPatch(name=payload.name.strip()))
    else:
        user = _auth_store().create_user(
            UserCreate(
                email=email,
                name=(payload.name or "").strip() or _derive_name_from_email(email),
                role="client",
                status="active",
            )
        )

    _auth_store().assign_client_access(
        UserClientAccessCreate(
            user_id=user.id,
            client_id=UUID(row["client_id"]),
            role="client",
        )
    )
    password = (payload.password or "").strip()
    if len(password) < 8:
        password = f"{secrets.token_urlsafe(18)}Aa1"
    _auth_store().set_password(user.id, password)
    session = _auth_store().issue_session(SessionIssueRequest(user_id=user.id, ttl_minutes=settings.oauth_session_ttl_minutes))

    with sqlite_conn(settings.budgets_db_path) as conn:
        conn.execute(
            """
            UPDATE client_invites
            SET status='accepted', accepted_user_id=?, accepted_at=?, updated_at=?
            WHERE id=?
            """,
            (str(user.id), now.isoformat(), now.isoformat(), row["id"]),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM client_invites WHERE id=?", (row["id"],)).fetchone()

    client = _client_store().get(UUID(row["client_id"]))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return ClientInviteAcceptResponse(
        invite=_to_client_invite_out(updated),
        client=client,
        user=user,
        session=session,
    )


def _normalize_next_path(next_path: Optional[str]) -> str:
    value = (next_path or "/").strip()
    if not value.startswith("/"):
        return "/"
    if value.startswith("//"):
        return "/"
    return value


def _with_oauth_connect_options(
    next_path: str,
    *,
    connect_mode: str,
    connection_key: Optional[str],
) -> str:
    parsed = urlsplit(_normalize_next_path(next_path))
    query_items = [
        (k, v)
        for (k, v) in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in {"ops_connect_mode", "ops_connection_key"}
    ]
    mode = (connect_mode or "add").strip().lower()
    if mode not in {"add", "overwrite"}:
        mode = "add"
    query_items.append(("ops_connect_mode", mode))
    key = str(connection_key or "").strip()
    if mode == "overwrite" and key:
        query_items.append(("ops_connection_key", key))
    return urlunsplit(("", "", parsed.path or "/", urlencode(query_items, doseq=True), ""))


def _extract_oauth_connect_options(next_path: str) -> tuple[str, str, Optional[str]]:
    parsed = urlsplit(_normalize_next_path(next_path))
    mode = "add"
    key: Optional[str] = None
    clean_items: List[tuple[str, str]] = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        if k == "ops_connect_mode":
            candidate = str(v or "").strip().lower()
            if candidate in {"add", "overwrite"}:
                mode = candidate
            continue
        if k == "ops_connection_key":
            candidate = str(v or "").strip()
            if candidate:
                key = candidate
            continue
        clean_items.append((k, v))
    if mode == "overwrite" and not key:
        mode = "add"
    clean_next = urlunsplit(("", "", parsed.path or "/", urlencode(clean_items, doseq=True), ""))
    return clean_next, mode, key


def _cookie_samesite_value() -> str:
    val = (settings.auth_cookie_samesite or "lax").lower().strip()
    if val not in {"lax", "strict", "none"}:
        return "lax"
    return val


def _new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def _set_csrf_cookie(response: JSONResponse | RedirectResponse, value: str) -> None:
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=value,
        httponly=False,
        samesite=_cookie_samesite_value(),
        secure=settings.auth_cookie_secure,
        max_age=max(60, settings.oauth_session_ttl_minutes * 60),
        path="/",
    )


def _get_session_token(
    authorization: Optional[str],
    x_session_token: Optional[str],
    cookie_token: Optional[str],
    *,
    required: bool = True,
) -> Optional[str]:
    value = (authorization or "").strip()
    if value:
        if not value.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail={"code": "unauthorized", "message": "Authorization must use Bearer token"},
            )
        token = value[7:].strip()
        if not token:
            raise HTTPException(
                status_code=401,
                detail={"code": "unauthorized", "message": "Missing Bearer token"},
            )
        return token
    if x_session_token:
        return x_session_token.strip()
    if cookie_token:
        return cookie_token.strip()
    if not required:
        return None
    raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing session token"})


def auth_context(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> RequestContext:
    token = _get_session_token(
        authorization,
        x_session_token,
        request.cookies.get(settings.auth_cookie_name),
        required=True,
    )
    session = _auth_facade().get_session_context(token)
    if not session.valid or not session.user_id or not session.role:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthorized",
                "message": "Invalid or expired session",
                "details": {"reason": session.reason},
            },
        )
    return RequestContext(
        user_id=session.user_id,
        role=session.role,
        global_access=bool(session.global_access),
        accessible_client_ids=set(session.accessible_client_ids),
    )


def optional_auth_context(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Optional[RequestContext]:
    token = _get_session_token(
        authorization,
        x_session_token,
        request.cookies.get(settings.auth_cookie_name),
        required=False,
    )
    if not token:
        return None
    session = _auth_facade().get_session_context(token)
    if not session.valid or not session.user_id or not session.role:
        if _internal_admin_required():
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "unauthorized",
                    "message": "Invalid or expired session",
                    "details": {"reason": session.reason},
                },
            )
        # Local/dev behavior: ignore stale/invalid token for optional admin plumbing.
        return None
    return RequestContext(
        user_id=session.user_id,
        role=session.role,
        global_access=bool(session.global_access),
        accessible_client_ids=set(session.accessible_client_ids),
    )


def _internal_admin_required() -> bool:
    return settings.app_env.lower() in {"prod", "production"}


def _enforce_internal_admin(ctx: Optional[RequestContext]) -> None:
    if not _internal_admin_required():
        return
    if not ctx:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Admin session required"})
    ensure_admin(ctx)


def session_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> str:
    return _get_session_token(
        authorization,
        x_session_token,
        request.cookies.get(settings.auth_cookie_name),
        required=True,
    )


def current_session_context(token: str = Depends(session_token)) -> SessionContextResponse:
    session = _auth_facade().get_session_context(token)
    if not session.valid:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthorized",
                "message": "Invalid or expired session",
                "details": {"reason": session.reason},
            },
        )
    return session


def _account_or_404(account_id: UUID) -> AdAccountOut:
    account = _ad_account_store().get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Ad account not found")
    return account


def _resolve_discovery_client_id(ctx: RequestContext, requested_client_id: Optional[UUID]) -> UUID:
    if requested_client_id:
        ensure_client_access(ctx, requested_client_id)
        return requested_client_id
    if ctx.global_access:
        candidates = [c.id for c in _client_store().list(status="active")]
    else:
        candidates = [cid for cid in ctx.accessible_client_ids if _client_store().get(cid) is not None]
    if len(candidates) == 1:
        return candidates[0]
    raise HTTPException(
        status_code=400,
        detail={
            "code": "client_id_required",
            "message": "client_id is required for discovery when multiple clients are available",
            "details": {"client_count": len(candidates)},
        },
    )


def _infer_single_tenant_client(ctx: RequestContext) -> Optional[UUID]:
    if ctx.global_access:
        return None
    candidates = [cid for cid in ctx.accessible_client_ids if _client_store().get(cid) is not None]
    if len(candidates) == 1:
        return candidates[0]
    return None


@app.get("/health")
def health(ctx: Optional[RequestContext] = Depends(optional_auth_context)) -> dict:
    if settings.observability_public:
        accounts = load_accounts(settings)
        by_platform = {
            "meta": len([a for a in accounts if a.platform == "meta"]),
            "google": len([a for a in accounts if a.platform == "google"]),
            "tiktok": len([a for a in accounts if a.platform == "tiktok"]),
        }
        return {"status": "ok", "accounts": by_platform, "env": settings.app_env}
    if not ctx or ctx.role != "admin":
        return {"status": "ok"}
    accounts = load_accounts(settings)
    by_platform = {
        "meta": len([a for a in accounts if a.platform == "meta"]),
        "google": len([a for a in accounts if a.platform == "google"]),
        "tiktok": len([a for a in accounts if a.platform == "tiktok"]),
    }
    return {"status": "ok", "accounts": by_platform, "env": settings.app_env}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/readyz")
def readyz(ctx: Optional[RequestContext] = Depends(optional_auth_context)) -> dict:
    checks = {
        "client_store": bool(getattr(app.state, "client_store", None)),
        "ad_account_store": bool(getattr(app.state, "ad_account_store", None)),
        "ad_stats_store": bool(getattr(app.state, "ad_stats_store", None)),
        "budget_store": bool(getattr(app.state, "budget_store", None)),
        "auth_store": bool(getattr(app.state, "auth_store", None)),
    }
    db_ok = True
    db_error = None
    try:
        with sqlite_conn(settings.budgets_db_path) as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        db_ok = False
        db_error = type(exc).__name__
    checks["sqlite"] = db_ok

    if not all(checks.values()):
        if settings.observability_public:
            payload = {"status": "not_ready", "checks": checks}
            if db_error:
                payload["db_error"] = db_error
            return JSONResponse(status_code=503, content=payload)
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    if settings.observability_public:
        return {"status": "ready", "checks": checks}
    if ctx and ctx.role == "admin":
        return {"status": "ready", "checks": checks}
    return {"status": "ready"}


@app.get("/metrics")
def metrics(ctx: Optional[RequestContext] = Depends(optional_auth_context)):
    if not settings.observability_public:
        if not ctx or ctx.role != "admin":
            raise HTTPException(status_code=404, detail="Not found")
    snap = _runtime_metrics().snapshot()
    lines: list[str] = []
    lines.append("# HELP http_requests_total Total HTTP requests")
    lines.append("# TYPE http_requests_total counter")
    for (method, path, status), value in sorted(snap["requests_total"].items()):
        lines.append(
            f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {value}'
        )

    lines.append("# HELP http_request_duration_seconds_sum Total request duration seconds")
    lines.append("# TYPE http_request_duration_seconds_sum counter")
    for (method, path), value in sorted(snap["request_duration_seconds_sum"].items()):
        lines.append(
            f'http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {value:.6f}'
        )

    lines.append("# HELP http_request_duration_seconds_count Request duration sample count")
    lines.append("# TYPE http_request_duration_seconds_count counter")
    for (method, path), value in sorted(snap["request_duration_seconds_count"].items()):
        lines.append(
            f'http_request_duration_seconds_count{{method="{method}",path="{path}"}} {value}'
        )

    uptime = max(0.0, time.time() - float(snap["started_at"]))
    lines.append("# HELP app_uptime_seconds Application uptime")
    lines.append("# TYPE app_uptime_seconds gauge")
    lines.append(f"app_uptime_seconds {uptime:.2f}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/accounts")
def list_external_accounts(ctx: RequestContext = Depends(auth_context)) -> dict:
    ensure_admin(ctx)
    accounts = [a.model_dump() for a in load_accounts(settings)]
    return {"items": accounts, "count": len(accounts)}


@app.get(
    "/auth/access-model",
    summary="Get auth/role access model",
    description="Architecture-level model for internal authorization. External provider auth is separate from authorization.",
)
def auth_access_model(ctx: Optional[RequestContext] = Depends(optional_auth_context)):
    if not settings.observability_public:
        if not ctx or ctx.role != "admin":
            raise HTTPException(status_code=404, detail="Not found")
    return {
        "roles": ROLE_ACCESS_MODEL,
        "security_assumptions": [
            "external_authentication_does_not_grant_internal_authorization",
            "tenant_isolation_enforced_by_backend",
            "provider_identity_maps_to_single_internal_user",
        ],
    }


@app.post(
    "/auth/internal/users",
    response_model=UserOut,
    summary="[INTERNAL/TEMP] Create internal user",
    description="Temporary internal/admin-only plumbing endpoint for architecture validation.",
)
def auth_create_user(
    payload: UserCreate,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_store().create_user(payload)


@app.get(
    "/auth/internal/users",
    summary="[INTERNAL/TEMP] List internal users",
    description="Temporary internal/admin-only plumbing endpoint for architecture validation.",
)
def auth_list_users(ctx: Optional[RequestContext] = Depends(optional_auth_context)):
    _enforce_internal_admin(ctx)
    rows = _auth_store().list_users()
    return {"items": [x.model_dump(mode="json") for x in rows], "count": len(rows)}


@app.patch(
    "/auth/internal/users/{user_id}",
    response_model=UserOut,
    summary="[INTERNAL/TEMP] Patch internal user",
    description="Temporary internal/admin-only endpoint for internal user role/status updates.",
)
def auth_patch_user(
    user_id: UUID,
    payload: UserPatch,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_store().patch_user(user_id, payload)


@app.delete(
    "/auth/internal/users/{user_id}",
    summary="[INTERNAL/TEMP] Delete internal user",
    description="Temporary internal/admin-only endpoint for hard user removal and cleanup.",
)
def auth_delete_user(
    user_id: UUID,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    _auth_store().delete_user(user_id)
    return {"status": "deleted"}


@app.post(
    "/auth/internal/identities/link",
    response_model=AuthIdentityOut,
    summary="[INTERNAL/TEMP] Link external identity to internal user",
    description="Temporary internal/admin-only plumbing endpoint. No OAuth flow here.",
)
def auth_link_identity(
    payload: AuthIdentityLink,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_store().link_identity(payload)


@app.get("/auth/internal/identities", summary="List linked external identities")
def auth_list_identities(
    user_id: Optional[UUID] = None,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    rows = _auth_store().list_identities(user_id=user_id)
    return {"items": [x.model_dump(mode="json") for x in rows], "count": len(rows)}


@app.post(
    "/auth/internal/access",
    response_model=UserClientAccessOut,
    summary="[INTERNAL/TEMP] Assign tenant access to user",
    description="Temporary internal/admin-only plumbing endpoint for architecture validation.",
)
def auth_assign_access(
    payload: UserClientAccessCreate,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    row = _auth_store().assign_client_access(payload)
    _audit_event(
        event_type="access.assigned",
        resource_type="user_client_access",
        resource_id=str(row.id),
        ctx=ctx,
        tenant_client_id=row.client_id,
        payload={"target_user_id": str(row.user_id), "role": row.role},
    )
    return row


@app.get("/auth/internal/access", summary="List tenant access assignments")
def auth_list_access(
    user_id: Optional[UUID] = None,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    rows = _auth_store().list_client_access(user_id=user_id)
    return {"items": [x.model_dump(mode="json") for x in rows], "count": len(rows)}


@app.post(
    "/auth/internal/sessions/issue",
    response_model=SessionIssueResponse,
    summary="[INTERNAL/TEMP] Issue backend-owned session token",
    description="Temporary internal/admin-only plumbing endpoint for architecture validation.",
)
def auth_issue_session(
    payload: SessionIssueRequest,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_store().issue_session(payload)


@app.post(
    "/auth/internal/sessions/validate",
    response_model=SessionValidationResponse,
    summary="[INTERNAL/TEMP] Validate backend-owned session token",
    description="Temporary internal/admin-only plumbing endpoint for architecture validation.",
)
def auth_validate_session(
    payload: SessionValidateRequest,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_store().validate_session(payload.token)


@app.post(
    "/auth/internal/sessions/revoke",
    summary="[INTERNAL/TEMP] Revoke backend-owned session token",
    description="Temporary internal/admin-only plumbing endpoint for architecture validation.",
)
def auth_revoke_session(
    payload: SessionValidateRequest,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_store().revoke_session(payload.token)


@app.post(
    "/auth/internal/facade/external/resolve",
    response_model=ExternalIdentityResolveResponse,
    summary="[INTERNAL/TEMP] Resolve/create user from external identity",
    description=(
        "Provider-agnostic facade endpoint. No OAuth redirect/callback flow here. "
        "Resolves existing mapped identity or creates/links internal user, then optionally issues backend session.\n\n"
        "Conflict policy:\n"
        "- existing identity => resolves to same internal user\n"
        "- new identity + existing email => 409 by default (no auto-merge)\n"
        "- set `allow_email_merge=true` to merge by email explicitly"
    ),
)
def auth_facade_resolve_external(
    payload: ExternalIdentityResolveRequest,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_facade().resolve_or_create_from_external_identity(payload)


@app.post(
    "/auth/internal/facade/sessions/context",
    response_model=SessionContextResponse,
    summary="[INTERNAL/TEMP] Resolve current session/user context",
    description="Returns backend authorization context: role + global_access flag + accessible tenant client_ids.",
)
def auth_facade_session_context(
    payload: SessionValidateRequest,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_facade().get_session_context(payload.token)


@app.get(
    "/auth/me",
    response_model=AuthMeResponse,
    summary="Get current authenticated user/session context",
    description="Frontend-facing auth endpoint. Uses backend-owned internal session token.",
)
def auth_me(session: SessionContextResponse = Depends(current_session_context)):
    if not session.user_id:
        raise HTTPException(status_code=401, detail="Session has no user")
    user = _auth_store().get_user(session.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session user not found")
    return AuthMeResponse(user=user, session=session)


@app.post(
    "/auth/password/login",
    response_model=AuthMeResponse,
    summary="Password login",
    description="Authenticates user by email/password and issues backend session cookie.",
)
def auth_password_login(payload: AuthPasswordLoginRequest):
    user = _auth_store().authenticate_password(payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_credentials", "message": "Invalid email or password"},
        )
    issued = _auth_store().issue_session(
        SessionIssueRequest(
            user_id=user.id,
            ttl_minutes=settings.oauth_session_ttl_minutes,
            metadata={"auth_method": "password"},
        )
    )
    session_ctx = _auth_facade().get_session_context(issued.token)
    if not session_ctx.valid:
        raise HTTPException(status_code=500, detail="Session context failed")
    body = AuthMeResponse(user=user, session=session_ctx)
    response = JSONResponse(content=body.model_dump(mode="json"))
    max_age = max(60, int((issued.expires_at - datetime.utcnow()).total_seconds()))
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=issued.token,
        httponly=True,
        samesite=_cookie_samesite_value(),
        secure=settings.auth_cookie_secure,
        max_age=max_age,
        path="/",
    )
    _set_csrf_cookie(response, _new_csrf_token())
    return response


@app.get(
    "/auth/csrf",
    summary="Get CSRF token for cookie-auth requests",
    description=(
        "Returns CSRF token for cross-domain SPA deployments where frontend cannot read API-domain cookies. "
        "Requires authenticated session."
    ),
)
def auth_csrf(request: Request, session: SessionContextResponse = Depends(current_session_context)):
    if not session.valid:
        raise HTTPException(status_code=401, detail="Invalid session")
    csrf_token = request.cookies.get(settings.csrf_cookie_name) or _new_csrf_token()
    response = JSONResponse(
        content={
            "csrf_token": csrf_token,
            "header_name": settings.csrf_header_name,
            "cookie_name": settings.csrf_cookie_name,
        }
    )
    _set_csrf_cookie(response, csrf_token)
    return response


@app.post(
    "/auth/logout",
    summary="Logout current session",
    description="Revokes current backend-owned session token. Idempotent.",
)
def auth_logout(token: str = Depends(session_token)):
    result = _auth_store().revoke_session(token)
    resp = JSONResponse(content={"status": "ok"})
    resp.delete_cookie(settings.auth_cookie_name, path="/")
    resp.delete_cookie(settings.csrf_cookie_name, path="/")
    if result.get("status") == "not_found":
        return resp
    return resp


@app.post(
    "/auth/session/refresh",
    summary="Refresh current session expiry",
    description="Extends active session expiry using backend refresh policy.",
)
def auth_refresh_session(request: Request, token: str = Depends(session_token)):
    refreshed = _auth_store().refresh_session(token, ttl_minutes=settings.oauth_session_refresh_ttl_minutes)
    if not refreshed.valid:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthorized",
                "message": "Session refresh failed",
                "details": {"reason": refreshed.reason},
            },
        )
    response = JSONResponse(
        content={
            "status": "ok",
            "expires_at": refreshed.expires_at.isoformat() if refreshed.expires_at else None,
        }
    )
    if refreshed.expires_at:
        max_age = max(60, int((refreshed.expires_at - datetime.utcnow()).total_seconds()))
        response.set_cookie(
            key=settings.auth_cookie_name,
            value=token,
            httponly=True,
            samesite=_cookie_samesite_value(),
            secure=settings.auth_cookie_secure,
            max_age=max_age,
            path="/",
        )
        _set_csrf_cookie(response, request.cookies.get(settings.csrf_cookie_name) or _new_csrf_token())
    return response


@app.get(
    "/auth/{provider}/start",
    summary="Start OAuth login",
    description="Starts OAuth authorization redirect for configured provider.",
)
def auth_oauth_start(
    provider: str,
    next_path: Optional[str] = Query(default="/", alias="next"),
    connect_mode: str = Query(default="add", pattern="^(add|overwrite)$"),
    connection_key: Optional[str] = Query(default=None),
):
    adapters = _oauth_adapters()
    adapter = adapters.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail="Unsupported auth provider")
    cfg = _oauth_provider_config_or_400(provider)
    normalized_next = _with_oauth_connect_options(
        _normalize_next_path(next_path),
        connect_mode=connect_mode,
        connection_key=connection_key,
    )
    nonce = secrets.token_urlsafe(24)
    state = _oauth_state_store().create_state(
        provider=provider,
        next_path=normalized_next,
        nonce=nonce,
        ttl_minutes=settings.oauth_state_ttl_minutes,
    )
    url = adapter.build_authorize_url(cfg, state.state)
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        key=settings.oauth_nonce_cookie_name,
        value=nonce,
        httponly=True,
        samesite=_cookie_samesite_value(),
        secure=settings.auth_cookie_secure,
        max_age=max(60, settings.oauth_state_ttl_minutes * 60),
        path="/",
    )
    return response


@app.get(
    "/auth/{provider}/callback",
    summary="OAuth callback",
    description=(
        "Completes OAuth login, resolves internal user/identity, issues backend session, "
        "then redirects to frontend login completion route."
    ),
)
def auth_oauth_callback(
    request: Request,
    provider: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    adapters = _oauth_adapters()
    adapter = adapters.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail="Unsupported auth provider")
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth callback parameters")
    nonce = request.cookies.get(settings.oauth_nonce_cookie_name)
    if not nonce:
        raise HTTPException(status_code=400, detail="Missing OAuth nonce")

    consumed = _oauth_state_store().consume_state(provider=provider, state=state, nonce=nonce)
    cfg = _oauth_provider_config_or_400(provider)
    identity = adapter.fetch_identity(cfg, code)
    # If user is already authenticated, treat this as provider connect/link for current user.
    current_token = _get_session_token(
        request.headers.get("Authorization"),
        request.headers.get("X-Session-Token"),
        request.cookies.get(settings.auth_cookie_name),
        required=False,
    )
    current_ctx = _auth_facade().get_session_context(current_token) if current_token else None

    if current_ctx and current_ctx.valid and current_ctx.user_id:
        current_user = _auth_store().get_user(current_ctx.user_id)
        if not current_user:
            raise HTTPException(status_code=401, detail="Session user not found")
        linked = _auth_store().link_identity(
            AuthIdentityLink(
                user_id=current_user.id,
                provider=provider,
                provider_user_id=identity.provider_user_id,
                email=identity.email,
                email_verified=identity.email_verified,
                raw_profile=identity.raw_profile,
            )
        )
        issued = _auth_facade().issue_session(current_user.id, ttl_minutes=settings.oauth_session_ttl_minutes)
        resolved = ExternalIdentityResolveResponse(user=current_user, identity=linked, session=issued)
    else:
        resolved = _auth_facade().resolve_or_create_from_external_identity(
            ExternalIdentityResolveRequest(
                provider=provider,
                provider_user_id=identity.provider_user_id,
                email=identity.email,
                email_verified=identity.email_verified,
                name=identity.name,
                raw_profile=identity.raw_profile,
                default_role="client",
                issue_session=True,
                session_ttl_minutes=settings.oauth_session_ttl_minutes,
            )
        )
    if not resolved.session:
        raise HTTPException(status_code=500, detail="OAuth session was not issued")

    redirect_next, connect_mode, requested_connection_key = _extract_oauth_connect_options(consumed.next_path or "/")

    _auto_upsert_integration_credentials(
        provider=provider,
        user=resolved.user,
        provider_user_id=identity.provider_user_id,
        connect_mode=connect_mode,
        requested_connection_key=requested_connection_key,
        oauth_tokens=identity.oauth_tokens,
        cfg=cfg,
    )

    base = settings.frontend_base_url.rstrip("/")
    next_encoded = quote(redirect_next or "/", safe="/?=&")
    token_fragment = quote(resolved.session.token, safe="")
    redirect_url = f"{base}/login/success?next={next_encoded}#token={token_fragment}"
    response = RedirectResponse(url=redirect_url, status_code=302)
    max_age = (
        max(60, int((resolved.session.expires_at - datetime.utcnow()).total_seconds()))
        if resolved.session and resolved.session.expires_at
        else 86400
    )
    # For local dev we keep secure=False; move to secure cookie in production HTTPS.
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=resolved.session.token,
        httponly=True,
        samesite=_cookie_samesite_value(),
        secure=settings.auth_cookie_secure,
        max_age=max_age,
        path="/",
    )
    _set_csrf_cookie(response, _new_csrf_token())
    response.delete_cookie(settings.oauth_nonce_cookie_name, path="/")
    return response


@app.post("/auth/provider-configs", response_model=AuthProviderConfigPublicOut, summary="Upsert auth provider config")
def auth_upsert_provider_config(
    payload: AuthProviderConfigCreate,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _to_public_provider_config(_auth_store().upsert_provider_config(payload))


@app.get("/auth/provider-configs", summary="List auth provider configs")
def auth_list_provider_configs(ctx: Optional[RequestContext] = Depends(optional_auth_context)):
    _enforce_internal_admin(ctx)
    rows = _auth_store().list_provider_configs()
    safe_rows = [_to_public_provider_config(x) for x in rows]
    return {"items": [x.model_dump(mode="json") for x in safe_rows], "count": len(safe_rows)}


@app.post(
    "/platform/integration-credentials",
    response_model=IntegrationCredentialPublicOut,
    summary="[INTERNAL/TEMP] Upsert tenant integration credential",
    description=(
        "Temporary internal/admin-only endpoint. Stores provider credentials at "
        "scope: global | agency | client. Runtime resolution priority: client -> agency -> global."
    ),
)
def create_integration_credential(
    payload: IntegrationCredentialCreate,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    payload_with_actor = payload.model_copy(update={"created_by": payload.created_by or ctx.user_id})
    out = _integration_credential_store().upsert(payload_with_actor)
    return _to_public_integration_credential(out)


@app.get(
    "/platform/integration-credentials",
    summary="[INTERNAL/TEMP] List tenant integration credentials",
    description="Temporary internal/admin-only endpoint for provider credential registry.",
)
def list_integration_credentials(
    status: str = Query(default="active", pattern="^(active|archived|all)$"),
    provider: Optional[str] = None,
    scope_type: Optional[str] = Query(default=None, pattern="^(global|agency|client)$"),
    scope_id: Optional[UUID] = None,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    rows = _integration_credential_store().list(
        status=status,
        provider=provider,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    safe_rows = [_to_public_integration_credential(x) for x in rows]
    return {"items": [x.model_dump(mode="json") for x in safe_rows], "count": len(safe_rows)}


@app.patch(
    "/platform/integration-credentials/{credential_id}",
    response_model=IntegrationCredentialPublicOut,
    summary="[INTERNAL/TEMP] Patch tenant integration credential",
    description="Temporary internal/admin-only endpoint for provider credential registry.",
)
def patch_integration_credential(
    credential_id: UUID,
    payload: IntegrationCredentialPatch,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    payload_with_actor = payload.model_copy(update={"created_by": payload.created_by or ctx.user_id})
    out = _integration_credential_store().patch(credential_id, payload_with_actor)
    return _to_public_integration_credential(out)


@app.delete(
    "/platform/integration-credentials/{credential_id}",
    response_model=IntegrationCredentialPublicOut,
    summary="[INTERNAL/TEMP] Archive tenant integration credential",
    description="Temporary internal/admin-only endpoint for provider credential registry.",
)
def archive_integration_credential(
    credential_id: UUID,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    out = _integration_credential_store().archive(credential_id)
    return _to_public_integration_credential(out)


@app.get(
    "/me/integration-connections",
    summary="List my visible integration connections",
    description="Agency sees only own agency-scope credentials. Admin sees all.",
)
def list_my_integration_connections(
    status: str = Query(default="active", pattern="^(active|archived|all)$"),
    provider: Optional[str] = None,
    ctx: RequestContext = Depends(auth_context),
):
    rows = _visible_integration_credentials_for_ctx(ctx, status=status, provider=provider)
    safe_rows = [_to_public_integration_credential(x) for x in rows]
    return {"items": [x.model_dump(mode="json") for x in safe_rows], "count": len(safe_rows)}


@app.delete(
    "/me/integration-connections/{credential_id}",
    response_model=IntegrationCredentialPublicOut,
    summary="Archive integration connection visible to current user",
)
def archive_my_integration_connection(
    credential_id: UUID,
    ctx: RequestContext = Depends(auth_context),
):
    rows = _integration_credential_store().list(status="all")
    target = next((x for x in rows if str(x.id) == str(credential_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Integration credential not found")
    _ensure_credential_manage_access(ctx, target)
    out = _integration_credential_store().archive(credential_id)
    return _to_public_integration_credential(out)


@app.post(
    "/platform/agencies",
    response_model=AgencyOut,
    summary="[INTERNAL/TEMP] Create agency tenant",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_create_agency(payload: AgencyCreate, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    return _platform_admin_store().create_agency(payload)


@app.get(
    "/platform/agencies",
    summary="[INTERNAL/TEMP] List agencies",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_list_agencies(
    status: str = Query(default="all", pattern="^(active|suspended|all)$"),
    ctx: RequestContext = Depends(auth_context),
):
    if ctx.role == "admin":
        rows = _platform_admin_store().list_agencies(status=status)
    elif ctx.role == "agency" and ctx.user_id:
        ids = _agency_scope_ids_for_user(ctx.user_id)
        rows = []
        for agency_id in ids:
            row = _platform_admin_store().get_agency(agency_id)
            if not row:
                continue
            if status != "all" and row.status != status:
                continue
            rows.append(row)
    else:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Agency/admin access required"})
    return {"items": [x.model_dump(mode="json") for x in rows], "count": len(rows)}


@app.get(
    "/platform/agencies/{agency_id}",
    response_model=AgencyOut,
    summary="[INTERNAL/TEMP] Get agency",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_get_agency(agency_id: UUID, ctx: RequestContext = Depends(auth_context)):
    _ensure_agency_member_access(ctx, agency_id)
    row = _platform_admin_store().get_agency(agency_id)
    if not row:
        raise HTTPException(status_code=404, detail="Agency not found")
    return row


@app.patch(
    "/platform/agencies/{agency_id}",
    response_model=AgencyOut,
    summary="[INTERNAL/TEMP] Update agency",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_patch_agency(agency_id: UUID, payload: AgencyPatch, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    return _platform_admin_store().patch_agency(agency_id, payload)


@app.delete(
    "/platform/agencies/{agency_id}",
    summary="[INTERNAL/TEMP] Delete agency",
    description=(
        "Temporary internal/admin-only endpoint for hard agency removal. "
        "Agency users are not deleted; their agency bindings are removed."
    ),
)
def platform_delete_agency(agency_id: UUID, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    _platform_admin_store().delete_agency(agency_id)
    return {"status": "deleted"}


@app.post(
    "/platform/agencies/{agency_id}/members",
    response_model=AgencyMemberOut,
    summary="[INTERNAL/TEMP] Upsert agency member",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_upsert_agency_member(
    agency_id: UUID,
    payload: AgencyMemberCreate,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    return _platform_admin_store().upsert_member(agency_id, payload)


@app.get(
    "/platform/agencies/{agency_id}/members",
    response_model=List[AgencyMemberOut],
    summary="[INTERNAL/TEMP] List agency members",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_list_agency_members(agency_id: UUID, ctx: RequestContext = Depends(auth_context)):
    _ensure_agency_member_access(ctx, agency_id)
    return _platform_admin_store().list_members(agency_id)


@app.post(
    "/platform/agencies/{agency_id}/clients",
    response_model=AgencyClientAccessOut,
    summary="[INTERNAL/TEMP] Assign client access to agency",
    description=(
        "Temporary internal/admin-only plumbing endpoint for agency provisioning. "
        "This grants tenant access to all active agency members via user_client_access materialization."
    ),
)
def platform_assign_agency_client(
    agency_id: UUID,
    payload: AgencyClientAccessCreate,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    row = _platform_admin_store().assign_client(agency_id, payload)
    _audit_event(
        event_type="agency.client_access.assigned",
        resource_type="agency_client_access",
        resource_id=str(row.id),
        ctx=ctx,
        tenant_client_id=row.client_id,
        payload={"agency_id": str(row.agency_id)},
    )
    return row


@app.get(
    "/platform/agencies/{agency_id}/clients",
    response_model=List[AgencyClientAccessOut],
    summary="[INTERNAL/TEMP] List agency tenant access",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_list_agency_clients(agency_id: UUID, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    return _platform_admin_store().list_clients(agency_id)


@app.post(
    "/platform/agencies/{agency_id}/invites",
    response_model=AgencyInviteIssueResponse,
    summary="[INTERNAL/TEMP] Issue agency member invite",
    description=(
        "Temporary internal/admin-only provisioning endpoint. "
        "Creates one-time invite token (stored as hash) to onboard agency user."
    ),
)
def platform_issue_agency_invite(
    agency_id: UUID,
    payload: AgencyInviteCreate,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    return _platform_admin_store().issue_invite(
        agency_id,
        payload,
        invited_by=ctx.user_id,
        frontend_base_url=settings.frontend_base_url,
    )


@app.get(
    "/platform/agencies/{agency_id}/invites",
    response_model=List[AgencyInviteOut],
    summary="[INTERNAL/TEMP] List agency invites",
    description="Temporary internal/admin-only provisioning endpoint.",
)
def platform_list_agency_invites(
    agency_id: UUID,
    status: str = Query(default="all", pattern="^(pending|accepted|revoked|expired|all)$"),
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    return _platform_admin_store().list_invites(agency_id, status=status)


@app.post(
    "/platform/agencies/{agency_id}/invites/{invite_id}/revoke",
    response_model=AgencyInviteOut,
    summary="[INTERNAL/TEMP] Revoke agency invite",
)
def platform_revoke_agency_invite(
    agency_id: UUID,
    invite_id: UUID,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    return _platform_admin_store().revoke_invite(agency_id, invite_id)


@app.post(
    "/platform/agencies/{agency_id}/invites/{invite_id}/resend",
    response_model=AgencyInviteIssueResponse,
    summary="[INTERNAL/TEMP] Resend agency invite",
)
def platform_resend_agency_invite(
    agency_id: UUID,
    invite_id: UUID,
    payload: AgencyInviteResendRequest,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    return _platform_admin_store().resend_invite(
        agency_id,
        invite_id,
        payload,
        invited_by=ctx.user_id,
        frontend_base_url=settings.frontend_base_url,
    )


@app.post(
    "/platform/agencies/{agency_id}/members/{member_id}/deactivate",
    response_model=AgencyMemberOut,
    summary="[INTERNAL/TEMP] Deactivate agency member",
)
def platform_deactivate_agency_member(
    agency_id: UUID,
    member_id: UUID,
    ctx: RequestContext = Depends(auth_context),
):
    _ensure_agency_member_access(ctx, agency_id, manage=True)
    return _platform_admin_store().deactivate_member(agency_id, member_id)


@app.delete(
    "/platform/agencies/{agency_id}/members/{member_id}",
    summary="[INTERNAL/TEMP] Remove agency member",
)
def platform_remove_agency_member(
    agency_id: UUID,
    member_id: UUID,
    ctx: RequestContext = Depends(auth_context),
):
    _ensure_agency_member_access(ctx, agency_id, manage=True)
    _platform_admin_store().remove_member(agency_id, member_id)
    return {"status": "removed"}


@app.delete(
    "/platform/agencies/{agency_id}/clients/{access_id}",
    summary="[INTERNAL/TEMP] Revoke agency client binding",
)
def platform_revoke_agency_client(
    agency_id: UUID,
    access_id: UUID,
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    _platform_admin_store().revoke_client(agency_id, access_id)
    return {"status": "revoked"}


@app.post(
    "/auth/invites/accept",
    response_model=Union[AgencyInviteAcceptResponse, ClientInviteAcceptResponse],
    summary="Accept agency invite",
    description=(
        "Public onboarding endpoint: validates invite token and accepts agency/client invite, "
        "materializes tenant access, and issues backend session."
    ),
)
def auth_accept_agency_invite(payload: AgencyInviteAcceptRequest):
    try:
        accepted = _platform_admin_store().accept_invite(payload, session_ttl_minutes=settings.oauth_session_ttl_minutes)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "").strip().lower() if isinstance(detail, dict) else ""
        if not (
            (exc.status_code == 404 and code in {"invite_not_found", "invalid_invite"})
            or (exc.status_code == 400 and code in {"invalid_invite", "invite_expired", "invite_revoked", "invite_used"})
        ):
            raise
        accepted = _accept_client_invite(payload)
    response = JSONResponse(content=accepted.model_dump(mode="json"))
    max_age = max(60, int((accepted.session.expires_at - datetime.utcnow()).total_seconds()))
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=accepted.session.token,
        httponly=True,
        samesite=_cookie_samesite_value(),
        secure=settings.auth_cookie_secure,
        max_age=max_age,
        path="/",
    )
    _set_csrf_cookie(response, _new_csrf_token())
    return response


@app.post("/clients", response_model=ClientOut, summary="Create client")
def create_client(payload: ClientCreate, ctx: RequestContext = Depends(auth_context)):
    if ctx.role not in {"admin", "agency"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only admin/agency can create clients"},
        )
    agency_ids: List[UUID] = []
    if ctx.role == "agency":
        if not ctx.user_id:
            raise HTTPException(status_code=401, detail="Session user not found")
        agency_ids = _agency_scope_ids_for_user(ctx.user_id)
        if not agency_ids:
            raise HTTPException(
                status_code=403,
                detail={"code": "agency_unbound", "message": "Agency user has no active agency membership"},
            )
    created = _client_store().create(payload)
    if ctx.role == "agency":
        for agency_id in agency_ids:
            _platform_admin_store().assign_client(
                agency_id,
                AgencyClientAccessCreate(client_id=created.id),
            )
    return created


@app.get("/clients", summary="List clients")
def list_clients(
    status: str = Query(default="active", pattern="^(active|inactive|archived|all)$"),
    ctx: RequestContext = Depends(auth_context),
):
    items = _client_store().list(status=status)
    if not ctx.global_access:
        items = [x for x in items if x.id in ctx.accessible_client_ids]
    return {"items": [x.model_dump(mode="json") for x in items], "count": len(items)}


@app.get("/clients/{client_id}", response_model=ClientOut, summary="Get client")
def get_client(client_id: UUID, ctx: RequestContext = Depends(auth_context)):
    ensure_client_access(ctx, client_id)
    row = _client_store().get(client_id)
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")
    return row


@app.patch("/clients/{client_id}", response_model=ClientOut, summary="Update client")
def patch_client(client_id: UUID, payload: ClientPatch, ctx: RequestContext = Depends(auth_context)):
    ensure_client_access(ctx, client_id)
    return _client_store().patch(client_id, payload)


@app.delete("/clients/{client_id}", summary="Archive client")
def archive_client(client_id: UUID, ctx: RequestContext = Depends(auth_context)):
    ensure_client_access(ctx, client_id)
    row = _client_store().archive(client_id)
    return {"status": "archived", "client": row.model_dump(mode="json")}


@app.post(
    "/clients/{client_id}/invites",
    response_model=ClientInviteIssueResponse,
    summary="Issue client invite",
)
def issue_client_invite(client_id: UUID, payload: ClientInviteCreate, ctx: RequestContext = Depends(auth_context)):
    if ctx.role not in {"admin", "agency"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only admin/agency can issue client invites"},
        )
    ensure_client_access(ctx, client_id)
    return _issue_client_invite(
        client_id=client_id,
        email=payload.email,
        expires_in_days=payload.expires_in_days,
        invited_by=ctx.user_id,
    )


@app.get(
    "/clients/{client_id}/invites",
    response_model=List[ClientInviteOut],
    summary="List client invites",
)
def list_client_invites(
    client_id: UUID,
    status: str = Query(default="all", pattern="^(pending|accepted|revoked|expired|all)$"),
    ctx: RequestContext = Depends(auth_context),
):
    if ctx.role not in {"admin", "agency"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only admin/agency can list client invites"},
        )
    ensure_client_access(ctx, client_id)
    return _list_client_invites(client_id=client_id, status=status)


@app.post(
    "/clients/{client_id}/invites/{invite_id}/resend",
    response_model=ClientInviteIssueResponse,
    summary="Resend client invite",
)
def resend_client_invite(
    client_id: UUID,
    invite_id: UUID,
    payload: ClientInviteResendRequest,
    ctx: RequestContext = Depends(auth_context),
):
    if ctx.role not in {"admin", "agency"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only admin/agency can resend client invites"},
        )
    ensure_client_access(ctx, client_id)
    invites = _list_client_invites(client_id=client_id, status="all")
    target = next((x for x in invites if x.id == invite_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Client invite not found")
    _revoke_client_invite(client_id=client_id, invite_id=invite_id)
    return _issue_client_invite(
        client_id=client_id,
        email=target.email,
        expires_in_days=payload.expires_in_days,
        invited_by=ctx.user_id,
    )


@app.post(
    "/clients/{client_id}/invites/{invite_id}/revoke",
    response_model=ClientInviteOut,
    summary="Revoke client invite",
)
def revoke_client_invite(
    client_id: UUID,
    invite_id: UUID,
    ctx: RequestContext = Depends(auth_context),
):
    if ctx.role not in {"admin", "agency"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only admin/agency can revoke client invites"},
        )
    ensure_client_access(ctx, client_id)
    return _revoke_client_invite(client_id=client_id, invite_id=invite_id)


@app.post(
    "/ad-accounts",
    response_model=AdAccountOut,
    summary="Create ad account",
    description=(
        "Creates an internal ad-account mapping record (`client_id` + `platform` + `external_account_id`). "
        "This mapping is used for tenant grouping and spend attribution. "
        "It is NOT a source of client budget."
    ),
)
def create_ad_account(payload: AdAccountCreate, ctx: RequestContext = Depends(auth_context)):
    ensure_client_access(ctx, payload.client_id)
    return _ad_account_store().create(payload)


@app.get(
    "/ad-accounts",
    summary="List ad accounts",
    description=(
        "Returns internal ad-account mapping records. "
        "These records can be synced from providers and grouped under internal clients. "
        "Budget calculations are still based on internal `budgets`, not provider campaign budgets."
    ),
)
def list_ad_accounts(
    client_id: Optional[UUID] = None,
    status: str = Query(default="active", pattern="^(active|inactive|archived|all)$"),
    ctx: RequestContext = Depends(auth_context),
):
    if client_id:
        ensure_client_access(ctx, client_id)
    items = _ad_account_store().list(client_id=client_id, status=status)
    if not ctx.global_access and not client_id:
        items = [x for x in items if x.client_id in ctx.accessible_client_ids]
    return {"items": [x.model_dump(mode="json") for x in items], "count": len(items)}


@app.get("/ad-accounts/{account_id}", response_model=AdAccountOut, summary="Get ad account")
def get_ad_account(account_id: UUID, ctx: RequestContext = Depends(auth_context)):
    row = _ad_account_store().get(account_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ad account not found")
    ensure_account_access(ctx, row.client_id, account_id=row.id)
    return row


@app.patch("/ad-accounts/{account_id}", response_model=AdAccountOut, summary="Update ad account")
def patch_ad_account(account_id: UUID, payload: AdAccountPatch, ctx: RequestContext = Depends(auth_context)):
    existing = _account_or_404(account_id)
    ensure_account_access(ctx, existing.client_id, account_id=existing.id)
    if payload.client_id:
        ensure_client_access(ctx, payload.client_id)
    return _ad_account_store().patch(account_id, payload)


@app.delete("/ad-accounts/{account_id}", summary="Archive ad account")
def archive_ad_account(account_id: UUID, ctx: RequestContext = Depends(auth_context)):
    existing = _account_or_404(account_id)
    ensure_account_access(ctx, existing.client_id, account_id=existing.id)
    row = _ad_account_store().archive(account_id)
    return {"status": "archived", "ad_account": row.model_dump(mode="json")}


@app.post(
    "/ad-accounts/discover",
    response_model=AdAccountDiscoverResponse,
    summary="Discover/import ad accounts from providers",
    description=(
        "Discovers accessible accounts from provider APIs (Meta/Google/TikTok) and imports them into internal "
        "`ad_accounts` for a target client. Existing rows are matched by `(platform, external_account_id)` and "
        "updated when `upsert_existing=true`. Budget logic remains internal and independent."
    ),
)
def discover_ad_accounts(payload: AdAccountDiscoverRequest, ctx: RequestContext = Depends(auth_context)):
    if ctx.role not in {"admin", "agency"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only admin/agency can run account discovery"},
        )
    target_client_id = _resolve_discovery_client_id(ctx, payload.client_id)
    result = _ad_account_discovery_service().discover(
        provider=payload.provider,
        client_id=target_client_id,
        user_id=ctx.user_id,
        upsert_existing=payload.upsert_existing,
    )
    _process_discovery_alerts(
        target_client_id=target_client_id,
        providers_attempted=result.providers_attempted,
        providers_failed=result.providers_failed,
    )
    _audit_event(
        event_type="ad_accounts.discover",
        resource_type="ad_account",
        ctx=ctx,
        tenant_client_id=target_client_id,
        payload={
            "provider": payload.provider or "all",
            "client_id": str(target_client_id),
            "discovered": result.discovered,
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "providers_failed": result.providers_failed,
        },
    )
    return _ad_account_discovery_service().to_response(result)


@app.post(
    "/ad-accounts/sync/run",
    response_model=AdAccountSyncRunResponse,
    summary="Run ad-account sync",
    description=(
        "Runs provider sync for selected ad accounts and records sync jobs. "
        "Sync status/last_sync/error for account registry is derived from these jobs. "
        "If date_from/date_to are omitted, sync runs incrementally from each account's last_sync_at; "
        "for never-synced accounts it backfills the last 30 days."
    ),
)
def run_ad_accounts_sync(payload: AdAccountSyncRunRequest, ctx: RequestContext = Depends(auth_context)):
    if ctx.role not in {"admin", "agency"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Only admin/agency can run account sync"},
        )
    requested_accounts = _ad_account_store().list(status="all")
    if payload.client_id:
        ensure_client_access(ctx, payload.client_id)
        requested_accounts = [a for a in requested_accounts if a.client_id == payload.client_id]
    if payload.account_ids:
        requested_ids = set(payload.account_ids)
        requested_accounts = [a for a in requested_accounts if a.id in requested_ids]
    if payload.platform:
        requested_accounts = [a for a in requested_accounts if a.platform == payload.platform]

    if not ctx.global_access:
        disallowed = [a.id for a in requested_accounts if a.client_id not in ctx.accessible_client_ids]
        if disallowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "forbidden",
                    "message": "Requested account scope is outside allowed tenant access",
                    "details": {"account_ids": [str(x) for x in disallowed]},
                },
            )
        requested_accounts = [a for a in requested_accounts if a.client_id in ctx.accessible_client_ids]

    result = _ad_account_sync_service().run_sync(
        account_ids=[a.id for a in requested_accounts],
        platform=None,
        date_from=payload.date_from,
        date_to=payload.date_to,
        created_by=ctx.user_id,
        user_id=ctx.user_id,
        force=payload.force,
    )
    _process_sync_alerts(
        jobs=result.jobs,
        account_client_by_id={a.id: a.client_id for a in requested_accounts},
    )
    _audit_event(
        event_type="sync.run",
        resource_type="ad_account_sync",
        ctx=ctx,
        payload={
            "requested": result.requested,
            "processed": result.processed,
            "success": result.success,
            "failed": result.failed,
            "account_ids": [str(a.id) for a in requested_accounts],
            "client_id": str(payload.client_id) if payload.client_id else None,
            "platform_filter": payload.platform,
            "force": payload.force,
        },
    )
    return AdAccountSyncRunResponse(
        requested=result.requested,
        processed=result.processed,
        skipped=result.skipped,
        success=result.success,
        failed=result.failed,
        retry_scheduled=result.retry_scheduled,
        started_at=result.started_at,
        finished_at=result.finished_at,
        jobs=result.jobs,
    )


@app.get(
    "/ad-accounts/sync/jobs",
    summary="List ad-account sync jobs",
    description="Returns sync-job history with optional account/status filtering.",
)
def list_ad_account_sync_jobs(
    account_id: Optional[UUID] = None,
    status: str = Query(default="all", pattern="^(success|error|all)$"),
    limit: int = Query(default=50, ge=1, le=500),
    ctx: RequestContext = Depends(auth_context),
):
    if account_id:
        account = _account_or_404(account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
    rows = _ad_account_sync_service().list_jobs(account_id=account_id, status=status, limit=limit)

    if not ctx.global_access and not account_id:
        allowed = {a.id for a in _ad_account_store().list(status="all") if a.client_id in ctx.accessible_client_ids}
        rows = [r for r in rows if r.ad_account_id in allowed]

    return {"items": [r.model_dump(mode="json") for r in rows], "count": len(rows)}


@app.get(
    "/ad-accounts/sync/diagnostics",
    response_model=AdAccountSyncDiagnosticsResponse,
    summary="Get per-account sync diagnostics",
    description="Returns actionable sync diagnostics per account: state, safe error reason, retry status and next action hint.",
)
def ad_account_sync_diagnostics(
    client_id: Optional[UUID] = None,
    provider: Optional[str] = None,
    status: str = Query(default="active", pattern="^(active|inactive|archived|all)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    ctx: RequestContext = Depends(auth_context),
):
    if client_id:
        ensure_client_access(ctx, client_id)
    rows = _ad_account_store().list(client_id=client_id, status=status)
    if provider:
        p = provider.strip().lower()
        rows = [x for x in rows if (x.platform or "").lower().strip() == p]
    if not ctx.global_access:
        rows = [x for x in rows if x.client_id in ctx.accessible_client_ids]
    rows = rows[:limit]

    latest = _ad_account_sync_service().latest_by_account_ids([x.id for x in rows])
    client_names = {c.id: c.name for c in _client_store().list(status="all")}
    now = datetime.utcnow()
    items: List[AdAccountSyncDiagnosticOut] = []

    for account in rows:
        job = latest.get(account.id)
        if not job:
            state = "never_synced"
            message = "No sync jobs yet for this account."
            action = _sync_action_hint(state=state, error_code=None, retryable=False)
            items.append(
                AdAccountSyncDiagnosticOut(
                    ad_account_id=account.id,
                    client_id=account.client_id,
                    client_name=client_names.get(account.client_id),
                    platform=account.platform,
                    account_name=account.name,
                    account_status=account.status,
                    sync_state=state,
                    diagnostic_message=message,
                    action_hint=action,
                    last_sync_at=account.last_sync_at,
                )
            )
            continue

        if job.status == "success":
            state = "healthy"
            message = "Last sync completed successfully."
        elif job.retryable and job.next_retry_at and job.next_retry_at > now:
            state = "retry_scheduled"
            message = "Sync failed, retry is already scheduled."
        else:
            state = "error"
            message = _safe_sync_error_message(job.error_message)

        action = _sync_action_hint(state=state, error_code=job.error_code, retryable=bool(job.retryable))
        items.append(
            AdAccountSyncDiagnosticOut(
                ad_account_id=account.id,
                client_id=account.client_id,
                client_name=client_names.get(account.client_id),
                platform=account.platform,
                account_name=account.name,
                account_status=account.status,
                sync_state=state,
                diagnostic_message=message,
                action_hint=action,
                last_sync_at=account.last_sync_at or job.finished_at or job.started_at,
                last_job_id=job.id,
                last_job_status=job.status,
                records_synced=job.records_synced,
                error_code=job.error_code,
                error_category=job.error_category,
                retryable=bool(job.retryable),
                attempt=int(job.attempt or 1),
                next_retry_at=job.next_retry_at,
            )
        )

    summary = {
        "total_accounts": len(items),
        "healthy": len([x for x in items if x.sync_state == "healthy"]),
        "error": len([x for x in items if x.sync_state == "error"]),
        "retry_scheduled": len([x for x in items if x.sync_state == "retry_scheduled"]),
        "never_synced": len([x for x in items if x.sync_state == "never_synced"]),
    }
    return AdAccountSyncDiagnosticsResponse(summary=summary, items=items)


@app.get(
    "/integrations/overview",
    response_model=IntegrationsOverviewResponse,
    summary="Integrations hub overview",
    description=(
        "Returns provider integration health and recent integration events for frontend. "
        "Error texts are sanitized to avoid exposing raw provider/internal details in UI."
    ),
)
def integrations_overview(ctx: RequestContext = Depends(auth_context)):
    accounts = [a for a in _ad_account_store().list(status="all") if a.status != "archived"]
    if not ctx.global_access:
        accounts = [a for a in accounts if a.client_id in ctx.accessible_client_ids]
    account_ids = {a.id for a in accounts}

    jobs = _ad_account_sync_service().list_jobs(status="all", limit=500)
    jobs = [j for j in jobs if j.ad_account_id in account_ids]

    provider_configs = _auth_store().list_provider_configs()
    identities = _auth_store().list_identities()
    return build_integrations_overview(
        accounts=accounts,
        sync_jobs=jobs,
        provider_configs=provider_configs,
        identities=identities,
    )


@app.post(
    "/ad-stats/ingest",
    response_model=AdStatsIngestResponse,
    summary="Ingest daily ad stats",
    description=(
        "Upsert daily stats by (ad_account_id, date, platform). "
        "Optional `Idempotency-Key` header enables request replay safety for local validation."
    ),
    responses={409: {"description": "Idempotency key was reused with different request payload."}},
)
def ingest_ad_stats(
    payload: AdStatsIngestRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ctx: RequestContext = Depends(auth_context),
):
    if not ctx.global_access:
        for row in payload.rows:
            account = _account_or_404(row.ad_account_id)
            ensure_account_access(ctx, account.client_id, account_id=account.id)
    return _ad_stats_store().ingest(payload, idempotency_key=idempotency_key)


@app.get("/ad-stats", summary="List daily ad stats")
def list_ad_stats(
    client_id: Optional[UUID] = None,
    account_id: Optional[UUID] = None,
    platform: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    ctx: RequestContext = Depends(auth_context),
):
    if client_id:
        ensure_client_access(ctx, client_id)
    if account_id:
        account = _account_or_404(account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
        if client_id and client_id != account.client_id:
            raise HTTPException(status_code=400, detail="account_id does not belong to client_id")

    items = _ad_stats_store().list(
        client_id=client_id,
        account_id=account_id,
        platform=platform,
        date_from=date_from,
        date_to=date_to,
    )
    if not ctx.global_access and not client_id and not account_id:
        allowed = {a.id for a in _ad_account_store().list(status="all") if a.client_id in ctx.accessible_client_ids}
        items = [x for x in items if x.ad_account_id in allowed]
    return {"items": [x.model_dump(mode="json") for x in items], "count": len(items)}


@app.post(
    "/budgets",
    response_model=BudgetOut,
    summary="Create budget",
    description=(
        "Create a manual budget. Scope rules: `scope=client` requires `account_id=null`; "
        "`scope=account` requires `account_id`.\n\n"
        "Important: budgets are internal governance values and are not read from or written to ad platforms.\n\n"
        "If an active budget overlaps an existing active period in the same scope key "
        "(same client for client scope, same account for account scope), API returns `409`."
    ),
    responses={409: {"description": "Active budget overlap conflict for scope key."}},
)
def create_budget(payload: BudgetCreate, ctx: RequestContext = Depends(auth_context)):
    ensure_client_access(ctx, payload.client_id)
    if payload.account_id:
        account = _account_or_404(payload.account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
        if account.client_id != payload.client_id:
            raise HTTPException(status_code=400, detail="account_id must belong to client_id")
    row = _budget_store().create(payload)
    _audit_event(
        event_type="budget.created",
        resource_type="budget",
        resource_id=str(row.id),
        ctx=ctx,
        tenant_client_id=row.client_id,
        payload={"scope": row.scope, "amount": str(row.amount), "status": row.status},
    )
    return row


@app.get(
    "/budgets",
    summary="List budgets",
    description="By default returns only active budgets (`status=active`). Use `status=all` to include archived.",
)
def list_budgets(
    client_id: Optional[UUID] = None,
    account_id: Optional[UUID] = None,
    status: str = Query(default="active", pattern="^(active|archived|all)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    ctx: RequestContext = Depends(auth_context),
):
    if client_id:
        ensure_client_access(ctx, client_id)
    if account_id:
        account = _account_or_404(account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
    rows = _budget_store().list(
        client_id=client_id,
        account_id=account_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    if not ctx.global_access and not client_id and not account_id:
        rows = [r for r in rows if r.client_id in ctx.accessible_client_ids]
    return {"items": [r.model_dump(mode="json") for r in rows], "count": len(rows)}


@app.get("/budgets/{budget_id}", response_model=BudgetOut, summary="Get budget")
def get_budget(budget_id: UUID, ctx: RequestContext = Depends(auth_context)):
    row = _budget_store().get(budget_id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget not found")
    ensure_client_access(ctx, row.client_id)
    return row


@app.patch(
    "/budgets/{budget_id}",
    response_model=BudgetOut,
    summary="Update budget",
    description=(
        "Updates budget and increments version only when there is a meaningful business-field change. "
        "No-op patch leaves record unchanged and does not write history.\n\n"
        "Overlap validation (active budgets) applies on update; conflicts return `409`."
    ),
    responses={409: {"description": "Active budget overlap conflict for scope key."}},
)
def patch_budget(budget_id: UUID, payload: BudgetPatch, ctx: RequestContext = Depends(auth_context)):
    existing = _budget_store().get(budget_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Budget not found")
    ensure_client_access(ctx, existing.client_id)
    if payload.client_id:
        ensure_client_access(ctx, payload.client_id)
    target_account_id = payload.account_id if payload.account_id is not None else existing.account_id
    target_client_id = payload.client_id if payload.client_id is not None else existing.client_id
    if target_account_id:
        account = _account_or_404(target_account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
        if account.client_id != target_client_id:
            raise HTTPException(status_code=400, detail="account_id must belong to client_id")
    row = _budget_store().patch(budget_id, payload)
    _audit_event(
        event_type="budget.updated",
        resource_type="budget",
        resource_id=str(row.id),
        ctx=ctx,
        tenant_client_id=row.client_id,
        payload={"scope": row.scope, "amount": str(row.amount), "status": row.status, "version": row.version},
    )
    return row


@app.get("/budgets/{budget_id}/history", response_model=List[BudgetHistoryOut], summary="Get budget history")
def get_budget_history(budget_id: UUID, ctx: RequestContext = Depends(auth_context)):
    row = _budget_store().get(budget_id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget not found")
    ensure_client_access(ctx, row.client_id)
    return _budget_store().history(budget_id)


@app.delete("/budgets/{budget_id}", summary="Archive budget")
def delete_budget(budget_id: UUID, ctx: RequestContext = Depends(auth_context)):
    row = _budget_store().get(budget_id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget not found")
    ensure_client_access(ctx, row.client_id)
    row = _budget_store().archive(budget_id)
    _audit_event(
        event_type="budget.archived",
        resource_type="budget",
        resource_id=str(row.id),
        ctx=ctx,
        tenant_client_id=row.client_id,
        payload={"scope": row.scope, "status": row.status},
    )
    return {"status": "archived", "budget": row.model_dump(mode="json")}


@app.post("/budgets/{budget_id}/transfer", response_model=BudgetTransferResponse, summary="Transfer budget between accounts")
def transfer_budget(budget_id: UUID, payload: BudgetTransferRequest, ctx: RequestContext = Depends(auth_context)):
    source = _budget_store().get(budget_id)
    if not source:
        raise HTTPException(status_code=404, detail="Budget not found")
    ensure_client_access(ctx, source.client_id)
    if source.account_id:
        ensure_account_access(ctx, source.client_id, account_id=source.account_id)
    target_account = _account_or_404(payload.target_account_id)
    ensure_account_access(ctx, target_account.client_id, account_id=target_account.id)
    if target_account.client_id != source.client_id:
        raise HTTPException(status_code=400, detail="target account must belong to same client as source budget")
    result = _budget_store().transfer(budget_id, payload)
    _audit_event(
        event_type="budget.transferred",
        resource_type="budget_transfer",
        resource_id=str(result.source_budget.id),
        ctx=ctx,
        tenant_client_id=result.source_budget.client_id,
        payload={
            "source_budget_id": str(result.source_budget.id),
            "target_budget_id": str(result.target_budget.id),
            "transferred_amount": str(result.transferred_amount),
        },
    )
    return result


@app.get("/budgets/{budget_id}/transfers", response_model=List[BudgetTransferOut], summary="Get budget transfer history")
def get_budget_transfers(
    budget_id: UUID,
    direction: str = Query(default="all", pattern="^(all|incoming|outgoing)$"),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: RequestContext = Depends(auth_context),
):
    row = _budget_store().get(budget_id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget not found")
    ensure_client_access(ctx, row.client_id)
    return _budget_store().list_transfers(budget_id, direction=direction, limit=limit)


@app.get(
    "/insights/overview",
    response_model=OverviewResponse,
    summary="Unified dashboard overview",
    description=(
        "Unified spend+budget endpoint for frontend. Spend source is normalized `ad_stats`; "
        "budget source is internal `budgets` table only. "
        "Provider campaign budgets are intentionally ignored.\n\n"
        "This endpoint does not write budgets to Meta/Google/TikTok; it is analytics/governance only. "
        "Filters: client_id (optional), account_id (optional), date range.\n\n"
        "Financial semantics:\n"
        "- expected_spend_to_date = budget * (elapsed_days / total_days)\n"
        "- forecast_spend = spend / (elapsed_days / total_days)\n"
        "- pace_delta = spend - expected_spend_to_date\n"
        "- pace_delta_percent = (pace_delta / expected_spend_to_date) * 100, null when expected=0\n"
        "- UTC date basis, inclusive day count"
    ),
)
def insights_overview(
    date_from: date,
    date_to: date,
    client_id: Optional[UUID] = None,
    account_id: Optional[UUID] = None,
    as_of_date: Optional[date] = None,
    ctx: RequestContext = Depends(auth_context),
):
    if client_id:
        ensure_client_access(ctx, client_id)
    if account_id:
        account = _account_or_404(account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
        if client_id and client_id != account.client_id:
            raise HTTPException(status_code=400, detail="account_id does not belong to client_id")
        if not client_id:
            client_id = account.client_id
    if not ctx.global_access and not client_id:
        inferred_client_id = _infer_single_tenant_client(ctx)
        if inferred_client_id:
            client_id = inferred_client_id
    if not ctx.global_access and not client_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Tenant scope required for non-admin context"},
        )
    return _overview_service().dashboard_overview(
        date_from=date_from,
        date_to=date_to,
        client_id=client_id,
        account_id=account_id,
        as_of_date=as_of_date,
    )


@app.get(
    "/insights/operational",
    response_model=OperationalInsightsResponse,
    summary="Operational insights and action recommendations",
    description=(
        "Returns recommendation cards generated from current spend efficiency and pacing metrics. "
        "Decision thresholds are configured via backend settings (not hardcoded in route handlers)."
    ),
)
def insights_operational(
    date_from: date,
    date_to: date,
    client_id: Optional[UUID] = None,
    account_id: Optional[UUID] = None,
    as_of_date: Optional[date] = None,
    ctx: RequestContext = Depends(auth_context),
):
    if client_id:
        ensure_client_access(ctx, client_id)
    if account_id:
        account = _account_or_404(account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
        if client_id and client_id != account.client_id:
            raise HTTPException(status_code=400, detail="account_id does not belong to client_id")
        if not client_id:
            client_id = account.client_id
    if not ctx.global_access and not client_id:
        inferred_client_id = _infer_single_tenant_client(ctx)
        if inferred_client_id:
            client_id = inferred_client_id
    if not ctx.global_access and not client_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Tenant scope required for non-admin context"},
        )

    overview = _overview_service().dashboard_overview(
        date_from=date_from,
        date_to=date_to,
        client_id=client_id,
        account_id=account_id,
        as_of_date=as_of_date,
    )
    return _operational_insights_service().generate(
        date_from=date_from,
        date_to=date_to,
        scope_client_id=overview["scope"]["client_id"],
        scope_account_id=overview["scope"]["account_id"],
        breakdown_accounts=overview["breakdowns"]["accounts"],
        budget_summary=overview["budget_summary"],
    )


@app.post(
    "/insights/operational/actions",
    response_model=OperationalActionOut,
    summary="Queue operational action from recommendation card",
    description="Executes/queues an action selected from operational insights cards and stores action event in backend.",
)
def execute_operational_action(payload: OperationalActionExecuteRequest, ctx: RequestContext = Depends(auth_context)):
    resolved_client_id = payload.client_id
    resolved_account_id = payload.account_id

    if payload.scope == "account" and not resolved_account_id:
        try:
            resolved_account_id = UUID(payload.scope_id)
        except Exception:
            raise HTTPException(status_code=400, detail="account scope requires account_id or UUID scope_id")

    if resolved_account_id:
        account = _account_or_404(resolved_account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
        if resolved_client_id and resolved_client_id != account.client_id:
            raise HTTPException(status_code=400, detail="account_id must belong to client_id")
        if not resolved_client_id:
            resolved_client_id = account.client_id

    if resolved_client_id:
        ensure_client_access(ctx, resolved_client_id)

    payload = payload.model_copy(update={"client_id": resolved_client_id, "account_id": resolved_account_id})
    return _operational_action_store().create(payload, created_by=ctx.user_id)


@app.get(
    "/insights/operational/actions",
    response_model=List[OperationalActionOut],
    summary="List queued/executed operational actions",
)
def list_operational_actions(
    client_id: Optional[UUID] = None,
    account_id: Optional[UUID] = None,
    scope: Optional[str] = Query(default=None, pattern="^(account|client|agency)$"),
    status: Optional[str] = Query(default=None, pattern="^(queued|applied|failed)$"),
    ctx: RequestContext = Depends(auth_context),
):
    if client_id:
        ensure_client_access(ctx, client_id)
    if account_id:
        account = _account_or_404(account_id)
        ensure_account_access(ctx, account.client_id, account_id=account.id)
        if not client_id:
            client_id = account.client_id
    if not ctx.global_access and not client_id:
        inferred_client_id = _infer_single_tenant_client(ctx)
        if inferred_client_id:
            client_id = inferred_client_id
    if not ctx.global_access and not client_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Tenant scope required for non-admin context"},
        )
    return _operational_action_store().list(
        client_id=client_id,
        account_id=account_id,
        scope=scope,
        status=status,
    )


@app.get("/agency/overview", response_model=AgencyOverviewResponse, summary="Agency aggregation overview")
def agency_overview(date_from: date, date_to: date, ctx: RequestContext = Depends(auth_context)):
    return _overview_service().agency_overview(
        date_from=date_from,
        date_to=date_to,
        allowed_client_ids=None if ctx.global_access else ctx.accessible_client_ids,
    )


@app.get("/audit/logs", response_model=List[AuditLogOut], summary="List audit trail events")
def list_audit_logs(
    event_type: Optional[str] = None,
    actor_user_id: Optional[UUID] = None,
    tenant_client_id: Optional[UUID] = None,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    return _audit_log_store().list(
        event_type=event_type,
        actor_user_id=actor_user_id,
        tenant_client_id=tenant_client_id,
        limit=limit,
    )


@app.get(
    "/alerts",
    response_model=List[AlertOut],
    summary="List operational alerts",
    description="Internal alert feed foundation. Delivery channels (Telegram/Slack/Email) can subscribe later.",
)
def list_alerts(
    status: str = Query(default="open", pattern="^(open|acked|resolved|all)$"),
    severity: Optional[str] = Query(default=None, pattern="^(critical|high|medium|low)$"),
    provider: Optional[str] = None,
    client_id: Optional[UUID] = None,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: RequestContext = Depends(auth_context),
):
    ensure_admin(ctx)
    if client_id:
        ensure_client_access(ctx, client_id)
    rows = _alert_store().list(
        status=status,
        severity=severity,
        provider=provider,
        client_id=client_id,
        limit=limit,
    )
    if ctx.role != "admin":
        rows = [x for x in rows if x.client_id and x.client_id in ctx.accessible_client_ids]
    return rows


@app.post("/alerts/{alert_id}/ack", response_model=AlertOut, summary="Acknowledge alert")
def acknowledge_alert(alert_id: UUID, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    current = _alert_store().get(alert_id)
    if not current:
        raise HTTPException(status_code=404, detail="Alert not found")
    _ensure_alert_access(ctx, current)
    return _alert_store().acknowledge(alert_id, by_user_id=ctx.user_id)


@app.post("/alerts/{alert_id}/resolve", response_model=AlertOut, summary="Resolve alert manually")
def resolve_alert(alert_id: UUID, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    current = _alert_store().get(alert_id)
    if not current:
        raise HTTPException(status_code=404, detail="Alert not found")
    _ensure_alert_access(ctx, current)
    resolved = _alert_store().resolve_by_fingerprint(current.fingerprint)
    if not resolved:
        raise HTTPException(status_code=404, detail="Alert not found")
    return resolved


@app.get("/meta/insights", response_model=MetaInsightsResponse)
def meta_insights(date_from: str, date_to: str, account_id: Optional[str] = None, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    if not date_from or not date_to:
        raise HTTPException(status_code=400, detail="date_from and date_to are required")
    return get_meta_insights(load_accounts(settings), date_from, date_to, account_id)


@app.get("/google/insights", response_model=GoogleInsightsResponse)
def google_insights(date_from: str, date_to: str, account_id: Optional[str] = None, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    if not date_from or not date_to:
        raise HTTPException(status_code=400, detail="date_from and date_to are required")
    return get_google_insights(load_accounts(settings), date_from, date_to, account_id)


@app.get("/tiktok/insights", response_model=TikTokInsightsResponse)
def tiktok_insights(date_from: str, date_to: str, account_id: Optional[str] = None, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
    if not date_from or not date_to:
        raise HTTPException(status_code=400, detail="date_from and date_to are required")
    return get_tiktok_insights(load_accounts(settings), date_from, date_to, account_id)
