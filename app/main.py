from __future__ import annotations

import secrets
import threading
import time
from datetime import date, datetime
from typing import List, Optional
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response

from app.schemas import (
    AdAccountCreate,
    AdAccountOut,
    AdAccountPatch,
    AdAccountSyncJobOut,
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
    ClientOut,
    ClientPatch,
    AuthIdentityLink,
    AuthIdentityOut,
    AuthProviderConfigCreate,
    AuthProviderConfigOut,
    AuditLogOut,
    ExternalIdentityResolveRequest,
    ExternalIdentityResolveResponse,
    SessionIssueRequest,
    SessionIssueResponse,
    AuthMeResponse,
    SessionContextResponse,
    SessionValidateRequest,
    SessionValidationResponse,
    UserClientAccessCreate,
    UserClientAccessOut,
    UserCreate,
    UserOut,
    GoogleInsightsResponse,
    MetaInsightsResponse,
    IntegrationsOverviewResponse,
    OverviewResponse,
    TikTokInsightsResponse,
)
from app.services.ad_accounts import AdAccountStore, InMemoryAdAccountStore, SqliteAdAccountStore
from app.services.ad_account_sync import (
    AdAccountSyncService,
    InMemoryAdAccountSyncJobStore,
    SqliteAdAccountSyncJobStore,
)
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
from app.services.overview import OverviewService
from app.services.operational_insights import OperationalInsightsService
from app.services.operational_actions import (
    InMemoryOperationalActionStore,
    OperationalActionStore,
    SqliteOperationalActionStore,
)
from app.services.acl import RequestContext, ensure_account_access, ensure_admin, ensure_client_access
from app.services.audit_log import AuditLogStore, InMemoryAuditLogStore, SqliteAuditLogStore
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
app = FastAPI(title="Envidicy Digital Dashboard Backend", version="0.3.0")

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
ad_account_sync_job_store = SqliteAdAccountSyncJobStore(settings.budgets_db_path)
ad_account_sync_service = AdAccountSyncService(account_store=ad_account_store, job_store=ad_account_sync_job_store)
ad_stats_store: AdStatsStore = SqliteAdStatsStore(settings.budgets_db_path, ad_account_store)
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
app.state.ad_account_sync_service = ad_account_sync_service
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
            "/_testing/use-inmemory-stores",
            "/auth/invites/accept",
            "/auth/internal/sessions/issue",
            "/auth/internal/sessions/validate",
            "/auth/internal/sessions/revoke",
        }
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


@app.post("/_testing/use-inmemory-stores")
def use_inmemory_stores():
    # Helper for tests to avoid file I/O and cross-test state leakage.
    c = InMemoryClientStore()
    a = InMemoryAdAccountStore(c)
    sync_jobs = InMemoryAdAccountSyncJobStore()
    sync_service = AdAccountSyncService(account_store=a, job_store=sync_jobs)
    s = InMemoryAdStatsStore(a)
    b = InMemoryBudgetStore()
    auth = InMemoryAuthStore()
    platform_admin = InMemoryPlatformAdminStore(auth)
    oauth_states = InMemoryOAuthStateStore()
    app.state.client_store = c
    app.state.ad_account_store = a
    app.state.ad_account_sync_service = sync_service
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
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.runtime_metrics = RuntimeMetrics()
    return {"status": "ok"}


def _client_store() -> ClientStore:
    return app.state.client_store


def _ad_account_store() -> AdAccountStore:
    return app.state.ad_account_store


def _ad_stats_store() -> AdStatsStore:
    return app.state.ad_stats_store


def _ad_account_sync_service() -> AdAccountSyncService:
    return app.state.ad_account_sync_service


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


def _normalize_next_path(next_path: Optional[str]) -> str:
    value = (next_path or "/").strip()
    if not value.startswith("/"):
        return "/"
    if value.startswith("//"):
        return "/"
    return value


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
    if x_session_token:
        return x_session_token.strip()
    if cookie_token:
        return cookie_token.strip()
    if not authorization:
        if not required:
            return None
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing session token"})
    value = authorization.strip()
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


@app.get("/health")
def health() -> dict:
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
def readyz() -> dict:
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
        payload = {"status": "not_ready", "checks": checks}
        if db_error:
            payload["db_error"] = db_error
        return JSONResponse(status_code=503, content=payload)
    return {"status": "ready", "checks": checks}


@app.get("/metrics")
def metrics():
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
def auth_access_model():
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
):
    adapters = _oauth_adapters()
    adapter = adapters.get(provider)
    if not adapter:
        raise HTTPException(status_code=404, detail="Unsupported auth provider")
    cfg = _oauth_provider_config_or_400(provider)
    normalized_next = _normalize_next_path(next_path)
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

    base = settings.frontend_base_url.rstrip("/")
    next_encoded = quote(consumed.next_path or "/", safe="/?=&")
    redirect_url = f"{base}/login/success?next={next_encoded}"
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


@app.post("/auth/provider-configs", response_model=AuthProviderConfigOut, summary="Upsert auth provider config")
def auth_upsert_provider_config(
    payload: AuthProviderConfigCreate,
    ctx: Optional[RequestContext] = Depends(optional_auth_context),
):
    _enforce_internal_admin(ctx)
    return _auth_store().upsert_provider_config(payload)


@app.get("/auth/provider-configs", summary="List auth provider configs")
def auth_list_provider_configs(ctx: Optional[RequestContext] = Depends(optional_auth_context)):
    _enforce_internal_admin(ctx)
    rows = _auth_store().list_provider_configs()
    return {"items": [x.model_dump(mode="json") for x in rows], "count": len(rows)}


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
    ensure_admin(ctx)
    rows = _platform_admin_store().list_agencies(status=status)
    return {"items": [x.model_dump(mode="json") for x in rows], "count": len(rows)}


@app.get(
    "/platform/agencies/{agency_id}",
    response_model=AgencyOut,
    summary="[INTERNAL/TEMP] Get agency",
    description="Temporary internal/admin-only plumbing endpoint for agency provisioning.",
)
def platform_get_agency(agency_id: UUID, ctx: RequestContext = Depends(auth_context)):
    ensure_admin(ctx)
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
    ensure_admin(ctx)
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
    ensure_admin(ctx)
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
    ensure_admin(ctx)
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
    response_model=AgencyInviteAcceptResponse,
    summary="Accept agency invite",
    description=(
        "Public onboarding endpoint: validates invite token, creates/links agency user, "
        "adds agency membership, materializes tenant access, and issues backend session."
    ),
)
def auth_accept_agency_invite(payload: AgencyInviteAcceptRequest):
    accepted = _platform_admin_store().accept_invite(payload, session_ttl_minutes=settings.oauth_session_ttl_minutes)
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
    ensure_admin(ctx)
    return _client_store().create(payload)


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
    "/ad-accounts/sync/run",
    response_model=AdAccountSyncRunResponse,
    summary="Run ad-account sync",
    description=(
        "Runs provider sync for selected ad accounts and records sync jobs. "
        "Sync status/last_sync/error for account registry is derived from these jobs."
    ),
)
def run_ad_accounts_sync(payload: AdAccountSyncRunRequest, ctx: RequestContext = Depends(auth_context)):
    requested_accounts = _ad_account_store().list(status="all")
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
        force=payload.force,
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
