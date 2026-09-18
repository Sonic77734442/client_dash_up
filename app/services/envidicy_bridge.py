"""Dash projection of Envidicy identity and live My authority.

No email matching, automatic tenant migration, or persistent access grants.
The legacy auth path does not contact My and remains unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import UUID, uuid4, uuid5

import httpx
from fastapi import HTTPException

from app.runtime_db import runtime_conn
from app.schemas import SessionContextResponse, UserCreate
from app.services.envidicy_pilot import PilotSubjectDenied, load_pilot_subjects, require_pilot_subject

ISSUER = "https://id.envidicy.com/realms/envidicy"
MY_URL = "https://my.envidicy.com/products"
AUTHORITY_URL = "https://my.envidicy.com/api/v1/authority/resolve"
PRODUCT = "dash.analytics"
PERMISSIONS = frozenset({PRODUCT, PRODUCT + ".read", PRODUCT + ".manage"})
PRINCIPAL_NAMESPACE = UUID("1a624d45-998d-49ca-bc77-cf05c3395435")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def identity_key(issuer: str, subject: str) -> str:
    if issuer != ISSUER or not isinstance(subject, str) or not 1 <= len(subject) <= 512 or any(ord(c) < 32 for c in subject):
        raise ValueError("Invalid Envidicy identity")
    return json.dumps([issuer, subject], separators=(",", ":"), ensure_ascii=True)


def canonical_uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid canonical context")
    return value


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid authority timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Authority timestamps require a timezone")
    return parsed.astimezone(timezone.utc)


def _stored_instant(value: object) -> datetime:
    # The shared PostgreSQL compatibility adapter returns UTC timestamps with
    # no timezone suffix. This applies only to our database, never My input.
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _keys(value: object, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Invalid authority shape")
    return value


def _active_interval(value: dict, now: datetime) -> None:
    if value.get("status") != "active" or _instant(value.get("valid_from")) > now or _instant(value.get("valid_until")) <= now:
        raise ValueError("Inactive authority interval")


def validate_authority(payload: object, *, issuer: str, subject: str, request_id: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    base = {"contract_version", "request_id", "decision", "principal", "evaluated_at"}
    if not isinstance(payload, dict):
        raise ValueError("Invalid authority response")
    deny = payload.get("decision") == "deny"
    _keys(payload, base | ({"reason_code"} if deny else {"membership", "entitlements", "permissions", "valid_until", "authority_revision", "revocation"}))
    if payload["contract_version"] != "envidicy.authority.v1" or payload["request_id"] != request_id:
        raise ValueError("Authority request mismatch")
    principal = _keys(payload["principal"], {"iss", "sub"})
    if principal != {"iss": issuer, "sub": subject}:
        raise ValueError("Authority identity mismatch")
    evaluated = _instant(payload["evaluated_at"])
    if not now - timedelta(minutes=5) <= evaluated <= now + timedelta(seconds=30):
        raise ValueError("Stale authority response")
    if deny:
        if not isinstance(payload["reason_code"], str) or not 1 <= len(payload["reason_code"]) <= 100:
            raise ValueError("Invalid authority denial")
        return {"access_state": "not_granted", "permissions": [], "redirect_to_my": True}
    if payload["decision"] != "allow":
        raise ValueError("Unknown authority decision")
    until = _instant(payload["valid_until"])
    if not now < until <= min(now, evaluated) + timedelta(seconds=120):
        raise ValueError("Expired authority response")
    membership = _keys(payload["membership"], {"status", "kind", "organization_id", "organization_name", "project_id", "project_name", "brand_id", "brand_name", "timezone", "roles", "version", "valid_from", "valid_until"})
    if membership["kind"] != "direct":
        raise ValueError("Direct membership required")
    organization_id = canonical_uuid(membership["organization_id"])
    project_id = canonical_uuid(membership["project_id"])
    _active_interval(membership, now)
    if until > _instant(membership["valid_until"]):
        raise ValueError("Authority exceeds membership")
    for key in ("organization_name", "project_name", "timezone"):
        if not isinstance(membership[key], str) or not 1 <= len(membership[key]) <= 255:
            raise ValueError("Invalid membership fields")
    if not isinstance(membership["roles"], list) or not membership["roles"] or len(membership["roles"]) > 16 or any(not isinstance(role, str) or not 1 <= len(role) <= 64 for role in membership["roles"]):
        raise ValueError("Invalid membership roles")
    grants = payload["entitlements"]
    if not isinstance(grants, list) or len(grants) != 1:
        raise ValueError("Exactly one Dash entitlement required")
    grant = _keys(grants[0], {"code", "status", "version", "valid_from", "valid_until"})
    if grant["code"] != PRODUCT:
        raise ValueError("Wrong product entitlement")
    _active_interval(grant, now)
    if until > _instant(grant["valid_until"]):
        raise ValueError("Authority exceeds entitlement")
    permissions = payload["permissions"]
    if not isinstance(permissions, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in permissions) or len(set(permissions)) != len(permissions):
        raise ValueError("Invalid product permissions")
    revocation = _keys(payload["revocation"], {"membership_generation", "entitlement_generation", "checked_at"})
    for version in (membership["version"], grant["version"], revocation["membership_generation"], revocation["entitlement_generation"]):
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 2**53 - 1:
            raise ValueError("Invalid authority generation")
    if revocation["membership_generation"] != membership["version"] or revocation["entitlement_generation"] != grant["version"] or revocation["checked_at"] != payload["evaluated_at"]:
        raise ValueError("Invalid authority revocation snapshot")
    if not isinstance(payload["authority_revision"], str) or not 1 <= len(payload["authority_revision"]) <= 128:
        raise ValueError("Invalid authority revision")
    # A malformed allow is an unavailable dependency, not a confirmed denial.
    # Finish validating its complete snapshot before offering a handoff to My.
    if not {PRODUCT, PRODUCT + ".read"}.issubset(permissions):
        return {"access_state": "not_granted", "permissions": [], "redirect_to_my": True}
    return {"access_state": "ready", "permissions": permissions, "redirect_to_my": False, "organization_id": organization_id,
            "project_id": project_id, "organization_name": membership["organization_name"],
            "project_name": membership["project_name"], "valid_until": until.isoformat(),
            "authority_revision": payload["authority_revision"]}


def _authority_token() -> str:
    direct = os.getenv("ENVIDICY_MY_AUTHORITY_TOKEN", "").strip()
    filename = os.getenv("ENVIDICY_MY_AUTHORITY_TOKEN_FILE", "").strip()
    if direct and filename:
        raise ValueError("Configure one authority credential source")
    if filename:
        with Path(filename).open("r", encoding="utf-8") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError("Authority credential unavailable")
        direct = raw.strip()
    if not 32 <= len(direct) <= 4096 or any(c.isspace() for c in direct):
        raise ValueError("Authority credential unavailable")
    return direct


class MyAuthorityClient:
    def __init__(self, *, client: httpx.Client | None = None, token: str | None = None):
        self.client = client
        self._token = token

    def resolve(self, issuer: str, subject: str) -> dict:
        identity_key(issuer, subject)
        request_id = str(uuid4())
        token = self._token if self._token is not None else _authority_token()
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json", "Accept-Encoding": "identity",
                   "Cache-Control": "no-store", "X-Request-ID": request_id}
        body = {"contract_version": "envidicy.authority.v1", "request_id": request_id,
                "product": PRODUCT, "principal": {"iss": issuer, "sub": subject}}
        own = self.client is None
        client = self.client or httpx.Client(timeout=httpx.Timeout(5, connect=3), trust_env=False, follow_redirects=False)
        try:
            deadline = time.monotonic() + 8
            with client.stream("POST", AUTHORITY_URL, headers=headers, json=body, follow_redirects=False) as response:
                if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                    raise ValueError("Authority unavailable")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 32768 or time.monotonic() > deadline:
                        raise ValueError("Authority response too large")
                def unique_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("Duplicate authority field")
                        result[key] = value
                    return result
                return validate_authority(json.loads(chunks, object_pairs_hook=unique_object), issuer=issuer, subject=subject, request_id=request_id)
        finally:
            if own:
                client.close()


class SqlEnvidicyStore:
    def __init__(self, auth_store):
        self.auth_store = auth_store
        self.db_path = auth_store.db_path

    def principal(self, user_id: UUID) -> dict | None:
        with runtime_conn(self.db_path) as conn:
            row = conn.execute("SELECT issuer, subject FROM envidicy_id_principals WHERE user_id=?", (str(user_id),)).fetchone()
        return dict(row) if row else None

    def resolve_identity(self, issuer: str, subject: str, name: str | None = None):
        key = identity_key(issuer, subject)
        now = utcnow().isoformat()
        user_id = str(uuid5(PRINCIPAL_NAMESPACE, key))
        with runtime_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT user_id FROM envidicy_id_principals WHERE issuer=? AND subject=?", (issuer, subject)).fetchone()
            if row:
                user_id = str(row["user_id"])
            else:
                # Separate principal with no email/password/grants. Existing users
                # can be linked only by a future explicitly reviewed migration.
                if conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
                    raise ValueError("Identity projection conflict")
                conn.execute("INSERT INTO users(id,email,name,role,status,created_at,updated_at) VALUES (?,NULL,?,'client','active',?,?)",
                             (user_id, (name or "Envidicy ID")[:255], now, now))
                conn.execute("INSERT INTO envidicy_id_principals(user_id,issuer,subject,created_at) VALUES (?,?,?,?)", (user_id, issuer, subject, now))
        user = self.auth_store.get_user(UUID(user_id))
        if not user or user.status != "active":
            raise HTTPException(status_code=403, detail="Envidicy principal inactive")
        return user

    def binding(self, organization_id: str, project_id: str) -> UUID | None:
        with runtime_conn(self.db_path) as conn:
            row = conn.execute("SELECT client_id FROM envidicy_project_bindings WHERE organization_id=? AND project_id=? AND status='active'",
                               (organization_id, project_id)).fetchone()
        return UUID(str(row["client_id"])) if row else None

    def bind_project(self, organization_id: str, project_id: str, client_id: str, *, operator_ref: str, apply: bool = False) -> dict:
        for value in (organization_id, project_id, client_id):
            canonical_uuid(value)
        if not operator_ref.strip() or len(operator_ref) > 128:
            raise ValueError("Operator reference required")
        with runtime_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            client = conn.execute("SELECT status FROM clients WHERE id=?", (client_id,)).fetchone()
            if not client or client["status"] != "active":
                raise ValueError("An existing active Dash client is required")
            existing = conn.execute("SELECT organization_id, project_id, client_id, status FROM envidicy_project_bindings WHERE project_id=? OR client_id=?", (project_id, client_id)).fetchall()
            if existing:
                if len(existing) != 1 or any(str(existing[0][k]) != v for k, v in (("organization_id", organization_id), ("project_id", project_id), ("client_id", client_id))) or existing[0]["status"] != "active":
                    raise ValueError("Existing binding differs; automatic reassignment is forbidden")
                return {"status": "unchanged"}
            if apply:
                conn.execute("INSERT INTO envidicy_project_bindings(organization_id,project_id,client_id,status,operator_ref,created_at) VALUES (?,?,?,'active',?,?)",
                             (organization_id, project_id, client_id, operator_ref, utcnow().isoformat()))
        return {"status": "applied" if apply else "checked", "client_id": client_id, "project_id": project_id}

    def save_transaction(self, *, state: str, browser: str, nonce: str, verifier: str, next_path: str, kind: str = "login") -> None:
        now = utcnow()
        with runtime_conn(self.db_path) as conn:
            conn.execute("DELETE FROM envidicy_login_transactions WHERE expires_at<=?", (now.isoformat(),))
            conn.execute("INSERT INTO envidicy_login_transactions(state_hash,browser_hash,nonce,code_verifier,next_path,kind,expires_at) VALUES (?,?,?,?,?,?,?)",
                         (digest(state), digest(browser), nonce, verifier, next_path, kind, (now + timedelta(minutes=5)).isoformat()))

    def consume_transaction(self, state: str, browser: str, *, kind: str = "login") -> dict | None:
        with runtime_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM envidicy_login_transactions WHERE state_hash=?", (digest(state),)).fetchone()
            if not row or row["kind"] != kind or _stored_instant(row["expires_at"]) <= utcnow() or not hmac.compare_digest(row["browser_hash"], digest(browser)):
                return None
            conn.execute("DELETE FROM envidicy_login_transactions WHERE state_hash=?", (digest(state),))
        return dict(row)


class MemoryEnvidicyStore:
    def __init__(self, auth_store):
        self.auth_store = auth_store
        self.principals: dict[UUID, dict] = {}
        self.bindings: dict[tuple[str, str], UUID] = {}
        self.transactions: dict[str, dict] = {}
        self.lock = RLock()

    def principal(self, user_id):
        return self.principals.get(user_id)

    def resolve_identity(self, issuer, subject, name=None):
        identity_key(issuer, subject)
        with self.lock:
            for user_id, principal in self.principals.items():
                if principal == {"issuer": issuer, "subject": subject}:
                    user = self.auth_store.get_user(user_id)
                    if not user or user.status != "active":
                        raise HTTPException(status_code=403, detail="Envidicy principal inactive")
                    return user
            user = self.auth_store.create_user(UserCreate(email=None, name=(name or "Envidicy ID")[:255], role="client"))
            self.principals[user.id] = {"issuer": issuer, "subject": subject}
            return user

    def binding(self, organization_id, project_id):
        return self.bindings.get((organization_id, project_id))

    def save_transaction(self, *, state, browser, nonce, verifier, next_path, kind="login"):
        with self.lock:
            now = utcnow()
            self.transactions = {k: v for k, v in self.transactions.items() if v["expires_at"] > now}
            self.transactions[digest(state)] = dict(browser_hash=digest(browser), nonce=nonce, code_verifier=verifier, next_path=next_path, kind=kind, expires_at=now + timedelta(minutes=5))

    def consume_transaction(self, state, browser, *, kind="login"):
        with self.lock:
            row = self.transactions.get(digest(state))
            if not row or row["kind"] != kind or row["expires_at"] <= utcnow() or not hmac.compare_digest(row["browser_hash"], digest(browser)):
                return None
            return self.transactions.pop(digest(state))


@dataclass
class EnvidicyBridge:
    store: SqlEnvidicyStore | MemoryEnvidicyStore
    client_store: object
    authority: MyAuthorityClient = field(default_factory=MyAuthorityClient)
    enabled: Callable[[], bool] = lambda: os.getenv("ENVIDICY_ID_ENABLED", "false").strip().lower() in {"true", "1", "yes", "on"}

    def project_session(self, context: SessionContextResponse) -> SessionContextResponse:
        if not context.valid or not context.user_id:
            return context
        # Profile identity mappings never relabel pre-existing password/OAuth sessions.
        if context.auth_method != "envidicy_id":
            return context
        principal = self.store.principal(context.user_id)
        if principal is None:
            return context.model_copy(update={"valid": False, "reason": "envidicy_identity_unlinked",
                                              "role": "client", "global_access": False, "access_scope": "assigned",
                                              "accessible_client_ids": [], "auth_source": "envidicy_id", "authority": None})
        authority = {"product": PRODUCT, "my_url": MY_URL, "access_state": "context_unavailable", "redirect_to_my": False,
                     "permissions": [], "organization_id": None, "project_id": None}
        clients = []
        if self.enabled():
            try:
                # Recheck an edited pilot cohort for already-issued ID sessions.
                # Legacy sessions returned above never depend on this setting.
                require_pilot_subject(principal["issuer"], principal["subject"],
                                      subjects=load_pilot_subjects())
                authority.update(self.authority.resolve(principal["issuer"], principal["subject"]))
                if authority["access_state"] == "ready":
                    client_id = self.store.binding(authority["organization_id"], authority["project_id"])
                    client = self.client_store.get(client_id) if client_id else None
                    if client and client.status == "active":
                        clients = [client_id]
                    else:
                        authority.update(access_state="project_unlinked", permissions=[], redirect_to_my=False)
            except PilotSubjectDenied:
                authority.update(access_state="not_granted", permissions=[], redirect_to_my=False)
            except (ValueError, OSError, httpx.HTTPError, KeyError, TypeError):
                authority.update(access_state="context_unavailable", permissions=[], redirect_to_my=False)
        return context.model_copy(update={"role": "client", "global_access": False, "access_scope": "assigned",
                                         "accessible_client_ids": clients, "auth_source": "envidicy_id", "authority": authority})
