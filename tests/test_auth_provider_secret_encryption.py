from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db import sqlite_conn
from app.schemas import AuthProviderConfigCreate
from app.services.auth_arch import SqliteAuthStore
from app.services.auth_secret_crypto import (
    AUTH_SECRET_MARKER,
    AUTH_SECRET_MARKER_FIELD,
    AuthProviderSecretCipher,
    auth_provider_secret_key_id,
    is_encrypted_auth_provider_secret,
)
from app.services.credential_crypto import (
    CredentialDecryptionError,
    CredentialKeyring,
)
from scripts.rotate_auth_provider_secrets import rotate_auth_provider_secrets


def _keyring(active: str = "current") -> CredentialKeyring:
    return CredentialKeyring(
        keys={"old": bytes([17]) * 32, "current": bytes([29]) * 32},
        active_key_id=active,
    )


def _payload(secret: str = "oauth-super-secret") -> AuthProviderConfigCreate:
    return AuthProviderConfigCreate(
        provider="google",
        client_id="oauth-client-id",
        client_secret=secret,
        redirect_uri="https://dashboard.example.com/auth/google/callback",
        enabled=True,
    )


def test_auth_secret_envelope_has_independent_domain_and_authenticated_identity():
    cipher = AuthProviderSecretCipher(_keyring())
    first = cipher.encrypt("oauth-super-secret", config_id="config-1", provider="Google")
    second = cipher.encrypt("oauth-super-secret", config_id="config-1", provider="google")

    assert first != second
    assert "oauth-super-secret" not in first
    document = json.loads(first)
    assert document[AUTH_SECRET_MARKER_FIELD] == AUTH_SECRET_MARKER
    assert auth_provider_secret_key_id(first) == "current"
    assert cipher.decrypt(first, config_id="config-1", provider="google") == "oauth-super-secret"

    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(first, config_id="config-2", provider="google")
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(first, config_id="config-1", provider="facebook")
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(
            json.dumps({"__credential_envelope__": "encrypted-integration-credential"}),
            config_id="config-1",
            provider="google",
        )


def test_persistent_auth_store_encrypts_raw_value_and_internal_dump_excludes_secret(tmp_path):
    db_path = str(tmp_path / "auth.db")
    store = SqliteAuthStore(db_path, keyring=_keyring())
    created = store.upsert_provider_config(_payload())

    assert created.client_secret == "oauth-super-secret"
    assert "client_secret" not in created.model_dump()
    with sqlite_conn(db_path) as conn:
        raw = conn.execute(
            "SELECT id, client_secret FROM auth_provider_configs WHERE provider='google'"
        ).fetchone()
    assert raw["id"] == str(created.id)
    assert is_encrypted_auth_provider_secret(raw["client_secret"])
    assert "oauth-super-secret" not in raw["client_secret"]
    assert store.list_provider_configs()[0].client_secret == "oauth-super-secret"

    updated = store.upsert_provider_config(_payload("replacement-secret"))
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.client_secret == "replacement-secret"


def test_new_persistent_auth_secret_write_fails_closed_without_keyring(tmp_path):
    db_path = str(tmp_path / "auth-disabled.db")
    store = SqliteAuthStore(db_path, keyring=CredentialKeyring.disabled())

    with pytest.raises(HTTPException) as exc_info:
        store.upsert_provider_config(_payload())
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "auth_provider_secret_encryption_unavailable"
    with sqlite_conn(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_provider_configs").fetchone()[0] == 0


def test_legacy_plaintext_read_and_atomic_rotation_preserve_timestamps(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = str(tmp_path / "auth-rotation.db")
    SqliteAuthStore(db_path, keyring=CredentialKeyring.disabled())
    legacy_id = str(uuid4())
    old_id = str(uuid4())
    timestamp = datetime(2025, 1, 2, 3, 4, 5).isoformat()
    old_cipher = AuthProviderSecretCipher(_keyring("old"))
    old_envelope = old_cipher.encrypt("old-key-secret", config_id=old_id, provider="facebook")
    with sqlite_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO auth_provider_configs
            (id, provider, client_id, client_secret, redirect_uri, enabled, created_at, updated_at)
            VALUES (?, 'google', 'client-a', 'legacy-secret', 'https://example.com/google', ?, ?, ?)
            """,
            (legacy_id, True, timestamp, timestamp),
        )
        conn.execute(
            """
            INSERT INTO auth_provider_configs
            (id, provider, client_id, client_secret, redirect_uri, enabled, created_at, updated_at)
            VALUES (?, 'facebook', 'client-b', ?, 'https://example.com/facebook', ?, ?, ?)
            """,
            (old_id, old_envelope, True, timestamp, timestamp),
        )
        conn.commit()

    disabled = SqliteAuthStore(db_path, keyring=CredentialKeyring.disabled())
    with sqlite_conn(db_path) as conn:
        legacy_row = conn.execute(
            "SELECT * FROM auth_provider_configs WHERE id=?", (legacy_id,)
        ).fetchone()
    assert disabled._to_provider_config(legacy_row).client_secret == "legacy-secret"

    dry_run = rotate_auth_provider_secrets(db_path, keyring=_keyring(), dry_run=True)
    assert (dry_run.legacy, dry_run.stale_key, dry_run.rotated) == (1, 1, 0)
    with sqlite_conn(db_path) as conn:
        before = conn.execute(
            "SELECT client_secret FROM auth_provider_configs WHERE id=?", (legacy_id,)
        ).fetchone()[0]
    assert before == "legacy-secret"

    applied = rotate_auth_provider_secrets(db_path, keyring=_keyring(), dry_run=False)
    assert (applied.legacy, applied.stale_key, applied.rotated) == (1, 1, 2)
    with sqlite_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, client_secret, created_at, updated_at FROM auth_provider_configs ORDER BY id"
        ).fetchall()
    assert all(is_encrypted_auth_provider_secret(row["client_secret"]) for row in rows)
    assert all(auth_provider_secret_key_id(row["client_secret"]) == "current" for row in rows)
    assert all(row["created_at"] == timestamp and row["updated_at"] == timestamp for row in rows)

    repeated = rotate_auth_provider_secrets(db_path, keyring=_keyring(), dry_run=False)
    assert repeated.rotated == 0
    assert repeated.current == 2
