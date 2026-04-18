from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Protocol
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn


@dataclass
class OAuthProviderConfig:
    provider: str
    client_id: str
    client_secret: str
    redirect_uri: str
    enabled: bool


@dataclass
class OAuthState:
    state: str
    provider: str
    next_path: str
    nonce: str
    expires_at: datetime


@dataclass
class ExternalIdentityPayload:
    provider_user_id: str
    email: Optional[str]
    email_verified: Optional[bool]
    name: Optional[str]
    raw_profile: Dict[str, object]


class OAuthStateStore(Protocol):
    def create_state(self, provider: str, next_path: str, nonce: str, ttl_minutes: int = 10) -> OAuthState: ...
    def consume_state(self, provider: str, state: str, nonce: str) -> OAuthState: ...


class OAuthProviderAdapter(Protocol):
    provider: str

    def build_authorize_url(self, cfg: OAuthProviderConfig, state: str) -> str: ...
    def fetch_identity(self, cfg: OAuthProviderConfig, code: str) -> ExternalIdentityPayload: ...


class SqliteOAuthStateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    def create_state(self, provider: str, next_path: str, nonce: str, ttl_minutes: int = 10) -> OAuthState:
        now = datetime.utcnow()
        state = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=ttl_minutes)
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO oauth_states (state, provider, next_path, nonce, expires_at, used_at, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (state, provider, next_path, nonce, expires_at.isoformat(), now.isoformat()),
            )
            conn.commit()
        return OAuthState(state=state, provider=provider, next_path=next_path, nonce=nonce, expires_at=expires_at)

    def consume_state(self, provider: str, state: str, nonce: str) -> OAuthState:
        now = datetime.utcnow()
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM oauth_states WHERE state=? AND provider=?",
                (state, provider),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="Invalid OAuth state")
            if row["used_at"]:
                raise HTTPException(status_code=400, detail="OAuth state already used")
            if str(row["nonce"] or "") != str(nonce or ""):
                raise HTTPException(status_code=400, detail="OAuth nonce mismatch")

            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                raise HTTPException(status_code=400, detail="OAuth state expired")

            conn.execute("UPDATE oauth_states SET used_at=? WHERE state=?", (now.isoformat(), state))
            conn.commit()

        return OAuthState(
            state=row["state"],
            provider=row["provider"],
            next_path=row["next_path"] or "/",
            nonce=row["nonce"] or "",
            expires_at=expires_at,
        )


class InMemoryOAuthStateStore:
    def __init__(self):
        self.items: Dict[str, OAuthState] = {}
        self.used: set[str] = set()

    def create_state(self, provider: str, next_path: str, nonce: str, ttl_minutes: int = 10) -> OAuthState:
        state = secrets.token_urlsafe(32)
        rec = OAuthState(
            state=state,
            provider=provider,
            next_path=next_path,
            nonce=nonce,
            expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
        )
        self.items[state] = rec
        return rec

    def consume_state(self, provider: str, state: str, nonce: str) -> OAuthState:
        rec = self.items.get(state)
        if not rec or rec.provider != provider:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        if rec.nonce != nonce:
            raise HTTPException(status_code=400, detail="OAuth nonce mismatch")
        if state in self.used:
            raise HTTPException(status_code=400, detail="OAuth state already used")
        if rec.expires_at <= datetime.utcnow():
            raise HTTPException(status_code=400, detail="OAuth state expired")
        self.used.add(state)
        return rec


class FacebookOAuthAdapter:
    provider = "facebook"

    def build_authorize_url(self, cfg: OAuthProviderConfig, state: str) -> str:
        params = {
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "state": state,
            "scope": "email,public_profile",
            "response_type": "code",
        }
        return f"https://www.facebook.com/v19.0/dialog/oauth?{urlencode(params)}"

    def fetch_identity(self, cfg: OAuthProviderConfig, code: str) -> ExternalIdentityPayload:
        with httpx.Client(timeout=20.0) as client:
            token_resp = client.get(
                "https://graph.facebook.com/v19.0/oauth/access_token",
                params={
                    "client_id": cfg.client_id,
                    "client_secret": cfg.client_secret,
                    "redirect_uri": cfg.redirect_uri,
                    "code": code,
                },
            )
            if token_resp.status_code >= 400:
                raise HTTPException(status_code=400, detail="Facebook token exchange failed")
            token = token_resp.json().get("access_token")
            if not token:
                raise HTTPException(status_code=400, detail="Facebook access token missing")

            profile_resp = client.get(
                "https://graph.facebook.com/me",
                params={"fields": "id,name,email", "access_token": token},
            )
            if profile_resp.status_code >= 400:
                raise HTTPException(status_code=400, detail="Facebook profile fetch failed")
            profile = profile_resp.json()

        provider_user_id = str(profile.get("id") or "")
        if not provider_user_id:
            raise HTTPException(status_code=400, detail="Facebook profile id missing")

        email = profile.get("email")
        return ExternalIdentityPayload(
            provider_user_id=provider_user_id,
            email=str(email) if email else None,
            email_verified=None,
            name=str(profile.get("name") or "") or None,
            raw_profile=profile,
        )


class GoogleOAuthAdapter:
    provider = "google"

    def build_authorize_url(self, cfg: OAuthProviderConfig, state: str) -> str:
        params = {
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "consent",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    def fetch_identity(self, cfg: OAuthProviderConfig, code: str) -> ExternalIdentityPayload:
        with httpx.Client(timeout=20.0) as client:
            token_resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": cfg.client_id,
                    "client_secret": cfg.client_secret,
                    "redirect_uri": cfg.redirect_uri,
                    "grant_type": "authorization_code",
                    "code": code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_resp.status_code >= 400:
                raise HTTPException(status_code=400, detail="Google token exchange failed")
            token = token_resp.json().get("access_token")
            if not token:
                raise HTTPException(status_code=400, detail="Google access token missing")

            profile_resp = client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
            if profile_resp.status_code >= 400:
                raise HTTPException(status_code=400, detail="Google profile fetch failed")
            profile = profile_resp.json()

        provider_user_id = str(profile.get("sub") or "")
        if not provider_user_id:
            raise HTTPException(status_code=400, detail="Google profile id missing")

        return ExternalIdentityPayload(
            provider_user_id=provider_user_id,
            email=str(profile.get("email") or "") or None,
            email_verified=bool(profile.get("email_verified")) if profile.get("email_verified") is not None else None,
            name=str(profile.get("name") or "") or None,
            raw_profile=profile,
        )
