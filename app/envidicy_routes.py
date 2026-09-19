"""Opt-in ID login on the existing same-origin Dash API BFF."""
from __future__ import annotations

import posixpath
import re
import secrets
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.services import envidicy_oidc as oidc
from app.services.auth_cutover import auto_login_enabled, id_only_enabled
from app.services.envidicy_bridge import MY_URL
from app.services.envidicy_pilot import (
    PilotConfigurationError, PilotSubjectDenied, load_pilot_subjects, require_pilot_subject,
)

TRANSACTION_COOKIE = "ops_envidicy_tx"
OPAQUE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


def safe_next(value: str | None) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or len(value) > 1024:
        return "/portal"
    decoded = value
    for _ in range(6):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if unquote(decoded) != decoded or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded) or decoded.startswith("//"):
        return "/portal"
    parsed = urlsplit(decoded)
    normalized_path = posixpath.normpath(parsed.path)
    if (parsed.scheme or parsed.netloc or normalized_path in {"/api", "/auth", "/register"}
            or normalized_path.startswith(("/api/", "/auth/", "/login", "/register/"))):
        return "/portal"
    return value


def register_envidicy_routes(app, *, get_bridge, get_auth, get_context, settings, set_csrf):
    def config():
        cfg = oidc.EnvidicyOidcConfig.from_env()
        load_pilot_subjects(enabled=cfg.enabled)
        return cfg

    def finish(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.delete_cookie(TRANSACTION_COOKIE, path="/", secure=settings.auth_cookie_secure, httponly=True, samesite="lax")
        return response

    def failed(code="envidicy_auth_failed", next_path=None):
        params = {"oauth_error": code}
        if next_path is not None:
            params["next"] = safe_next(next_path)
        return finish(RedirectResponse("/login?" + urlencode(params), status_code=303))

    def remember(response, browser):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.set_cookie(TRANSACTION_COOKIE, browser, max_age=300, httponly=True,
                            secure=settings.auth_cookie_secure, samesite="lax", path="/")
        return response

    @app.get("/auth/envidicy/config")
    def envidicy_public_config():
        # The cutover policy is independent from ID health. A bad ID secret must
        # never reopen a local password form after the ID-only switch.
        id_only = id_only_enabled()
        auto_login = auto_login_enabled()
        local_auth_enabled = not id_only
        # Misconfiguration is visible as a disabled entry, never a secret value.
        try:
            enabled = config().enabled
        except (oidc.EnvidicyOidcError, PilotConfigurationError):
            enabled = False
        return JSONResponse({"enabled": enabled, "local_auth_enabled": local_auth_enabled,
                             "auto_login": enabled and (auto_login or id_only),
                             "login_url": "/api/backend/auth/envidicy/start", "my_url": MY_URL},
                            headers={"Cache-Control": "no-store"})

    @app.get("/auth/envidicy/start")
    def envidicy_start(request: Request):
        next_path = safe_next(request.query_params.get("next"))
        try:
            cfg = config()
            if not cfg.enabled:
                return failed("envidicy_auth_disabled", next_path)
            transaction = oidc.generate_login_transaction()
            browser = secrets.token_urlsafe(32)
            # Neither My IDs nor permissions are accepted from a launch URL.
            get_bridge().store.save_transaction(state=transaction.state, browser=browser, nonce=transaction.nonce,
                                                verifier=transaction.code_verifier, next_path=next_path)
            url = oidc.authorization_url(cfg, state=transaction.state, nonce=transaction.nonce, code_verifier=transaction.code_verifier)
            return remember(RedirectResponse(url, status_code=303), browser)
        except (oidc.EnvidicyOidcError, PilotConfigurationError):
            return failed("envidicy_auth_not_configured", next_path)

    @app.get("/auth/envidicy/callback")
    async def envidicy_callback(request: Request):
        state = request.query_params.get("state", "")
        browser = request.cookies.get(TRANSACTION_COOKIE, "")
        if len(request.query_params.getlist("state")) != 1 or not OPAQUE.fullmatch(state) or not OPAQUE.fullmatch(browser):
            return failed()
        transaction = get_bridge().store.consume_transaction(state, browser)
        if not transaction:
            return failed()
        next_path = safe_next(transaction["next_path"])
        if request.query_params.get("error") or len(request.query_params.getlist("code")) != 1:
            return failed(next_path=next_path)
        issued = None
        try:
            cfg = config()
            if not cfg.enabled:
                return failed("envidicy_auth_disabled", next_path)
            identity = await oidc.exchange_code(cfg, code=request.query_params["code"],
                                                code_verifier=transaction["code_verifier"], expected_nonce=transaction["nonce"])
            # Check the verified subject before creating any local projection or
            # replacing a legacy session. Browser input cannot select this cohort.
            require_pilot_subject(identity.issuer, identity.subject,
                                  subjects=load_pilot_subjects(enabled=cfg.enabled))
            # Optional email is deliberately ignored. Only verified (iss, sub)
            # creates a separate projection; linking legacy accounts comes later.
            user = get_bridge().store.resolve_identity(identity.issuer, identity.subject, identity.name)
            issued = get_auth().issue_envidicy_session(user.id)
            # My uses a bounded synchronous client. Resolve it off the event loop
            # and keep the original 15-minute issuance deadline, including waits.
            try:
                context = await run_in_threadpool(get_context, issued.token)
            except Exception:
                # A failed local context must not orphan a usable fresh session
                # or replace an existing login. Never expose exception contents.
                get_auth().revoke_session(issued.token)
                return failed(next_path=next_path)
            cfg = config()
            if not cfg.enabled:
                raise ValueError("Envidicy ID is disabled")
            require_pilot_subject(identity.issuer, identity.subject,
                                  subjects=load_pilot_subjects(enabled=cfg.enabled))
            if (not context.valid or context.user_id != user.id
                    or context.auth_method != "envidicy_id" or context.auth_source != "envidicy_id"):
                raise ValueError("Envidicy session is unavailable")
        except PilotSubjectDenied:
            if issued is not None:
                get_auth().revoke_session(issued.token)
            return failed("envidicy_pilot_only", next_path)
        except (oidc.EnvidicyOidcError, ValueError, HTTPException):
            if issued is not None:
                get_auth().revoke_session(issued.token)
            return failed(next_path=next_path)
        authority = context.authority or {}
        # Only a fully validated My decision may trigger an external handoff.
        # Pilot exclusion, an unbound project and an outage remain local gates.
        confirmed_denial = authority.get("access_state") == "not_granted" and authority.get("redirect_to_my") is True
        destination = MY_URL if confirmed_denial else next_path
        response = finish(RedirectResponse(destination, status_code=303))
        # Revoke the old browser session only after a complete successful exchange.
        old_token = request.cookies.get(settings.auth_cookie_name)
        if old_token:
            get_auth().revoke_session(old_token)
        response.set_cookie(settings.auth_cookie_name, issued.token, max_age=900, httponly=True,
                            secure=settings.auth_cookie_secure, samesite="lax", path="/")
        set_csrf(response, secrets.token_urlsafe(24))
        return response

    @app.post("/auth/envidicy/logout")
    def envidicy_logout(request: Request):
        # Existing application CSRF middleware covers this cookie-auth POST.
        # Match ordinary API token precedence; a bearer/X-session logout must
        # revoke that token, not silently leave it alive or revoke another cookie.
        authorization = (request.headers.get("Authorization") or "").strip()
        if authorization:
            if not authorization.lower().startswith("bearer ") or not authorization[7:].strip():
                raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "A Bearer session token is required"})
            token = authorization[7:].strip()
        else:
            token = (request.headers.get("X-Session-Token") or request.cookies.get(settings.auth_cookie_name) or "").strip()
        if token:
            get_auth().revoke_session(token)
        try:
            cfg = config()
            transaction = oidc.generate_login_transaction()
            browser = secrets.token_urlsafe(32)
            get_bridge().store.save_transaction(state=transaction.state, browser=browser, nonce="", verifier="", next_path="/login", kind="logout")
            url = oidc.logout_url(cfg, state=transaction.state)
            response = remember(JSONResponse({"logout_url": url}), browser)
        except (oidc.EnvidicyOidcError, PilotConfigurationError):
            response = JSONResponse({"logout_url": "/login"}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(settings.auth_cookie_name, path="/")
        response.delete_cookie(settings.csrf_cookie_name, path="/")
        return response

    @app.get("/auth/envidicy/logout/callback")
    def envidicy_logout_callback(request: Request):
        state = request.query_params.get("state", "")
        browser = request.cookies.get(TRANSACTION_COOKIE, "")
        if len(request.query_params.getlist("state")) != 1 or not OPAQUE.fullmatch(state) or not OPAQUE.fullmatch(browser) or not get_bridge().store.consume_transaction(state, browser, kind="logout"):
            return failed()
        return finish(RedirectResponse("/login", status_code=303))
