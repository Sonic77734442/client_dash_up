"""Narrow Dash OIDC relying party; product/session authority lives elsewhere.

Callers must bind state to the initiating browser, expire the transaction, and
atomically consume it before exchanging a code. Only (issuer, subject) identifies
an account: optional profile/email values must never link existing accounts.
No provider tokens are returned, persisted, or logged here.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import time
from typing import Mapping
from urllib.parse import quote_plus, urlencode, urlsplit

import httpx


ISSUER = "https://id.envidicy.com/realms/envidicy"
CLIENT_ID = "envidicy-dash"
PUBLIC_ORIGIN = "https://dash.envidicy.kz"
CALLBACK_PATH = "/api/backend/auth/envidicy/callback"
LOGOUT_CALLBACK_PATH = "/api/backend/auth/envidicy/logout/callback"
AUTHORIZATION_ENDPOINT = f"{ISSUER}/protocol/openid-connect/auth"
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
END_SESSION_ENDPOINT = f"{ISSUER}/protocol/openid-connect/logout"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 16 * 1024
CLOCK_TOLERANCE_SECONDS = 5
MAX_TOKEN_AGE_SECONDS = 600
EXCHANGE_TIMEOUT_SECONDS = 20
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")


class EnvidicyOidcError(ValueError):
    """Safe to report without leaking credentials, codes, tokens, or responses."""


@dataclass(frozen=True)
class EnvidicyOidcConfig:
    enabled: bool = False
    client_secret: str = field(default="", repr=False)
    public_origin: str = PUBLIC_ORIGIN
    allow_loopback: bool = field(default=False, repr=False)
    issuer: str = field(default=ISSUER, init=False)
    client_id: str = field(default=CLIENT_ID, init=False)

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        if (not isinstance(self.client_secret, str) or not 32 <= len(self.client_secret) <= 4096
                or any(char.isspace() for char in self.client_secret)):
            raise EnvidicyOidcError("Envidicy ID client secret is invalid")
        if self.public_origin == PUBLIC_ORIGIN:
            return
        try:
            parsed = urlsplit(self.public_origin)
            valid_loopback = (
                self.allow_loopback and parsed.scheme in {"http", "https"}
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                and parsed.username is None and parsed.password is None
                and parsed.path == "" and not parsed.query and not parsed.fragment
                and not any(char.isspace() for char in self.public_origin)
                and not any(char in self.public_origin for char in ("\\", "?", "#"))
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except (ValueError, TypeError):
            valid_loopback = False
        if not valid_loopback:
            raise EnvidicyOidcError("Envidicy ID callback origin is not permitted")

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_origin}{CALLBACK_PATH}"

    @property
    def post_logout_redirect_uri(self) -> str:
        return f"{self.public_origin}{LOGOUT_CALLBACK_PATH}"

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> EnvidicyOidcConfig:
        environment = os.environ if environment is None else environment
        enabled = str(environment.get("ENVIDICY_ID_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return cls()  # Disabled rollout must not read files or validate secrets.
        secret = environment.get("ENVIDICY_ID_CLIENT_SECRET", "")
        secret_file = environment.get("ENVIDICY_ID_CLIENT_SECRET_FILE", "")
        if secret and secret_file:
            raise EnvidicyOidcError("Configure exactly one Envidicy ID client secret source")
        if secret_file:
            try:
                with Path(secret_file).open("rb") as handle:
                    raw = handle.read(4097)
                if len(raw) > 4096:
                    raise ValueError("oversized")
                secret = raw.decode("utf-8").strip()
            except (OSError, UnicodeError, ValueError):
                raise EnvidicyOidcError("Envidicy ID client secret file is unavailable or invalid") from None
        return cls(enabled=True, client_secret=secret)


@dataclass(frozen=True)
class LoginTransaction:
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)


@dataclass(frozen=True)
class EnvidicyIdentity:
    issuer: str
    subject: str
    name: str | None = None
    email: str | None = field(default=None, repr=False)


def generate_login_transaction() -> LoginTransaction:
    return LoginTransaction(*(secrets.token_urlsafe(32) for _ in range(3)))


def _require_enabled(config: EnvidicyOidcConfig) -> None:
    if not config.enabled:
        raise EnvidicyOidcError("Envidicy ID login is disabled")


def _validate_opaque(value: str, label: str) -> None:
    if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
        raise EnvidicyOidcError(f"Envidicy ID {label} is invalid")


def _validate_verifier(value: str) -> None:
    if not isinstance(value, str) or not _VERIFIER.fullmatch(value):
        raise EnvidicyOidcError("Envidicy ID PKCE verifier is invalid")


def authorization_url(config: EnvidicyOidcConfig, *, state: str, nonce: str, code_verifier: str) -> str:
    _require_enabled(config)
    _validate_opaque(state, "state")
    _validate_opaque(nonce, "nonce")
    _validate_verifier(code_verifier)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return AUTHORIZATION_ENDPOINT + "?" + urlencode({
        "response_type": "code", "client_id": config.client_id,
        "redirect_uri": config.redirect_uri, "scope": "openid profile email",
        "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
    })


def logout_url(config: EnvidicyOidcConfig, *, state: str) -> str:
    """Without retaining an ID token, ID may ask the user to confirm SSO logout."""
    _require_enabled(config)
    _validate_opaque(state, "logout state")
    return END_SESSION_ENDPOINT + "?" + urlencode({
        "client_id": config.client_id, "post_logout_redirect_uri": config.post_logout_redirect_uri,
        "state": state,
    })


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON value")


def _json_object(raw: bytes) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        return payload
    except (ValueError, UnicodeError, RecursionError):
        raise EnvidicyOidcError("Envidicy ID returned invalid JSON") from None


async def _request_json(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    # Endpoints are constants, never taken from JWT headers, callbacks or discovery.
    if url not in {DISCOVERY_URL, TOKEN_ENDPOINT, JWKS_URI}:
        raise EnvidicyOidcError("Envidicy ID endpoint is not permitted")
    headers = {"accept": "application/json", "accept-encoding": "identity", "cache-control": "no-store"}
    headers.update(kwargs.pop("headers", {}))
    async with client.stream(method, url, headers=headers, follow_redirects=False, timeout=10.0, **kwargs) as response:
        if response.status_code != 200:
            raise EnvidicyOidcError("Envidicy ID request was rejected")
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise EnvidicyOidcError("Envidicy ID returned unsupported content encoding")
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise EnvidicyOidcError("Envidicy ID returned unsupported content type")
        length = response.headers.get("content-length")
        if length is not None:
            try:
                if not 0 <= int(length) <= MAX_RESPONSE_BYTES:
                    raise ValueError("oversized")
            except ValueError:
                raise EnvidicyOidcError("Envidicy ID response is too large") from None
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise EnvidicyOidcError("Envidicy ID response is too large")
        return _json_object(bytes(body))


def _validate_discovery(discovery: dict) -> None:
    expected = {
        "issuer": ISSUER, "authorization_endpoint": AUTHORIZATION_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT, "jwks_uri": JWKS_URI, "end_session_endpoint": END_SESSION_ENDPOINT,
    }
    if any(discovery.get(key) != value for key, value in expected.items()):
        raise EnvidicyOidcError("Envidicy ID discovery does not match the configured issuer")
    for key, required in {
        "id_token_signing_alg_values_supported": "RS256", "response_types_supported": "code",
        "code_challenge_methods_supported": "S256", "token_endpoint_auth_methods_supported": "client_secret_basic",
    }.items():
        values = discovery.get(key)
        if not isinstance(values, list) or required not in values:
            raise EnvidicyOidcError("Envidicy ID does not support the required login protocol")


def _jwt_segment(segment: str) -> dict:
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
            raise ValueError("invalid base64url")
        return _json_object(base64.b64decode(segment + "=" * (-len(segment) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError):
        raise EnvidicyOidcError("Envidicy ID token is invalid") from None


def _profile_string(value: object, maximum: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or _CONTROLS.search(value):
        return None
    return value.strip()


def validate_id_token(config: EnvidicyOidcConfig, *, id_token: str, expected_nonce: str, jwks: dict) -> EnvidicyIdentity:
    """Verify a short-lived ID assertion against an already bounded, trusted JWKS."""
    _require_enabled(config)
    _validate_opaque(expected_nonce, "nonce")
    try:
        import jwt  # Lazy: disabled legacy login does not depend on PyJWT loading.

        if not isinstance(id_token, str) or not 1 <= len(id_token) <= MAX_TOKEN_BYTES or id_token.count(".") != 2:
            raise EnvidicyOidcError("Envidicy ID token is invalid")
        encoded_header, encoded_claims, _signature = id_token.split(".")
        header, untrusted_claims = _jwt_segment(encoded_header), _jwt_segment(encoded_claims)
        kid = header.get("kid")
        if (header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 255
                or _CONTROLS.search(kid) or any(key in header for key in ("crit", "jku", "x5u", "jwk", "b64"))):
            raise EnvidicyOidcError("Envidicy ID signing header is invalid")
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
            raise EnvidicyOidcError("Envidicy ID signing keys are invalid")
        candidates = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid]
        if len(candidates) != 1:
            raise EnvidicyOidcError("Envidicy ID signing key is unavailable")
        key = candidates[0]
        if (key.get("kty") != "RSA" or key.get("use", "sig") != "sig" or key.get("alg", "RS256") != "RS256"
                or ("key_ops" in key and (not isinstance(key["key_ops"], list) or "verify" not in key["key_ops"]))):
            raise EnvidicyOidcError("Envidicy ID signing key is invalid")
        public_key = jwt.PyJWK.from_dict({"kty": "RSA", "n": key.get("n"), "e": key.get("e")}, algorithm="RS256").key
        if not 2048 <= public_key.key_size <= 8192:
            raise EnvidicyOidcError("Envidicy ID signing key size is invalid")
        for claim in ("iat", "exp", "nbf"):
            value = untrusted_claims.get(claim)
            if claim == "nbf" and value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise EnvidicyOidcError("Envidicy ID token timestamps are invalid")
        claims = jwt.decode(
            id_token, public_key, algorithms=["RS256"], issuer=config.issuer, audience=config.client_id,
            leeway=CLOCK_TOLERANCE_SECONDS,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
        )
        # Dash trusts no extra ID-token audiences; the access-token audience
        # mapper in ID is deliberately separate from ID-token authorization.
        if claims["aud"] not in (config.client_id, [config.client_id]):
            raise EnvidicyOidcError("Envidicy ID token audience is invalid")
        if "azp" in claims and claims["azp"] != config.client_id:
            raise EnvidicyOidcError("Envidicy ID token authorized party is invalid")
        nonce = claims["nonce"]
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce.encode("utf-8"), expected_nonce.encode("ascii")):
            raise EnvidicyOidcError("Envidicy ID token nonce is invalid")
        if time.time() - claims["iat"] > MAX_TOKEN_AGE_SECONDS + CLOCK_TOLERANCE_SECONDS or claims["exp"] <= claims["iat"]:
            raise EnvidicyOidcError("Envidicy ID token is not fresh")
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 255 or _CONTROLS.search(subject):
            raise EnvidicyOidcError("Envidicy ID subject is invalid")
        return EnvidicyIdentity(config.issuer, subject, _profile_string(claims.get("name"), 255), _profile_string(claims.get("email"), 320))
    except EnvidicyOidcError:
        raise
    except Exception:
        # JWT/crypto errors must not expose their response contents to logs/UI.
        raise EnvidicyOidcError("Envidicy ID token validation failed") from None


async def exchange_code(config: EnvidicyOidcConfig, *, code: str, code_verifier: str,
                        expected_nonce: str, client: httpx.AsyncClient | None = None) -> EnvidicyIdentity:
    _require_enabled(config)
    _validate_verifier(code_verifier)
    _validate_opaque(expected_nonce, "nonce")
    if not isinstance(code, str) or not 1 <= len(code) <= 4096 or _CONTROLS.search(code):
        raise EnvidicyOidcError("Envidicy ID authorization code is invalid")

    async def exchange(http: httpx.AsyncClient) -> EnvidicyIdentity:
        _validate_discovery(await _request_json(http, "GET", DISCOVERY_URL))
        credentials = f"{quote_plus(config.client_id)}:{quote_plus(config.client_secret)}".encode("ascii")
        response = await _request_json(http, "POST", TOKEN_ENDPOINT,
            headers={"authorization": "Basic " + base64.b64encode(credentials).decode("ascii")},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": config.redirect_uri, "code_verifier": code_verifier})
        token = response.get("id_token")
        if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_BYTES:
            raise EnvidicyOidcError("Envidicy ID token response is invalid")
        jwks = await _request_json(http, "GET", JWKS_URI)
        return validate_id_token(config, id_token=token, expected_nonce=expected_nonce, jwks=jwks)

    try:
        async with asyncio.timeout(EXCHANGE_TIMEOUT_SECONDS):
            if client is not None:
                return await exchange(client)
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False) as http:
                return await exchange(http)
    except EnvidicyOidcError:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise EnvidicyOidcError("Envidicy ID is temporarily unavailable") from None
