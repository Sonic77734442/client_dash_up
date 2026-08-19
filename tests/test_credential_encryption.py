import base64
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import (
    AdAccountCreate,
    ClientCreate,
    IntegrationCredentialCreate,
    IntegrationCredentialPatch,
)
from app.services.ad_accounts import SqliteAdAccountStore
from app.services.clients import SqliteClientStore
from app.services.credential_crypto import (
    ACTIVE_KEY_ID_ENV,
    KEYRING_ENV,
    CredentialDecryptionError,
    CredentialEncryptionConfigurationError,
    CredentialKeyring,
)
from app.services.integration_credentials import SqliteIntegrationCredentialStore
from app.services.auth_arch import SqliteAuthStore
from app.services.platform_admin import SqlitePlatformAdminStore
from scripts.rotate_integration_credentials import main as rotate_credentials_main


TOKEN = "meta-super-secret-access-token"


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _encoded_key(seed: int) -> str:
    return base64.urlsafe_b64encode(_key(seed)).decode("ascii").rstrip("=")


def _keyring(active: str = "current", **keys: int) -> CredentialKeyring:
    values = keys or {active: 7}
    return CredentialKeyring(
        keys={key_id: _key(seed) for key_id, seed in values.items()},
        active_key_id=active,
    )


def _credential(
    *,
    provider: str = "meta",
    connection_key: str = "default",
    scope_type: str = "global",
    scope_id=None,
    credentials=None,
):
    return IntegrationCredentialCreate(
        provider=provider,
        scope_type=scope_type,
        scope_id=scope_id,
        connection_key=connection_key,
        credentials=credentials or {"access_token": TOKEN, "meta_app_id": "3177364462435934"},
    )


def _raw_credentials(db_path, credential_id):
    with sqlite_conn(str(db_path)) as conn:
        return conn.execute(
            "SELECT credentials_json FROM integration_credentials WHERE id=?",
            (str(credential_id),),
        ).fetchone()["credentials_json"]


def _insert_legacy(db_path, *, credential_id, connection_key="legacy", token=TOKEN):
    init_sqlite(str(db_path))
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with sqlite_conn(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO integration_credentials
              (id, provider, scope_type, scope_id, connection_key, credentials_json,
               status, created_by, created_at, updated_at)
            VALUES (?, 'meta', 'global', NULL, ?, ?, 'active', NULL, ?, ?)
            """,
            (
                str(credential_id),
                connection_key,
                json.dumps({"access_token": token}),
                now,
                now,
            ),
        )
        conn.commit()


def _create_minimal_credential_db(db_path, *, credential_id, credentials_json: str):
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE integration_credentials (
              id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              scope_type TEXT NOT NULL,
              scope_id TEXT NULL,
              connection_key TEXT NOT NULL,
              credentials_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_by TEXT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO integration_credentials
              (id,provider,scope_type,scope_id,connection_key,credentials_json,
               status,created_by,created_at,updated_at)
            VALUES (?, 'meta', 'global', NULL, 'default', ?, 'active', NULL, ?, ?)
            """,
            (str(credential_id), credentials_json, now, now),
        )
        conn.commit()


def _read_only_db_snapshot(db_path):
    path = db_path.resolve()
    file_stat = path.stat()
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as conn:
        schema_version = conn.execute("PRAGMA schema_version").fetchone()[0]
        schema = tuple(
            conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
        )
        has_credentials = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='integration_credentials'"
        ).fetchone()
        data = (
            tuple(conn.execute("SELECT * FROM integration_credentials ORDER BY rowid").fetchall())
            if has_credentials
            else ()
        )
    return {
        "mtime_ns": file_stat.st_mtime_ns,
        "mode": file_stat.st_mode,
        "file_attributes": getattr(file_stat, "st_file_attributes", None),
        "schema_version": schema_version,
        "schema": schema,
        "data": data,
    }


def test_keyring_env_is_opt_in_strict_and_rotation_ready():
    assert CredentialKeyring.from_env({}).enabled is False
    configured = CredentialKeyring.from_env(
        {
            KEYRING_ENV: json.dumps({"2026-08": _encoded_key(1), "2026-09": _encoded_key(2)}),
            ACTIVE_KEY_ID_ENV: "2026-09",
        }
    )
    assert configured.enabled is True
    assert configured.active_key_id == "2026-09"
    assert configured.has_key("2026-08")

    with pytest.raises(CredentialEncryptionConfigurationError):
        CredentialKeyring.from_env({KEYRING_ENV: json.dumps({"only": _encoded_key(1)})})
    with pytest.raises(CredentialEncryptionConfigurationError):
        CredentialKeyring.from_env(
            {KEYRING_ENV: json.dumps({"short": _encoded_key(1)[:-4]}), ACTIVE_KEY_ID_ENV: "short"}
        )


def test_authenticated_envelope_hides_secret_and_is_bound_to_database_identity():
    ring = _keyring()
    context = {
        "credential_id": uuid4(),
        "provider": "meta",
        "scope_type": "global",
        "scope_id": None,
        "connection_key": "default",
    }
    envelope = ring.encrypt_json({"access_token": TOKEN}, aad_context=context)

    assert TOKEN not in json.dumps(envelope)
    assert ring.decrypt_json(envelope, aad_context=context) == {"access_token": TOKEN}

    transplanted_context = {**context, "credential_id": uuid4()}
    with pytest.raises(CredentialDecryptionError) as error:
        ring.decrypt_json(envelope, aad_context=transplanted_context)
    assert TOKEN not in str(error.value)

    tampered = dict(envelope)
    tampered["ciphertext"] = str(tampered["ciphertext"])[:-1] + (
        "A" if not str(tampered["ciphertext"]).endswith("A") else "B"
    )
    with pytest.raises(CredentialDecryptionError) as error:
        ring.decrypt_json(tampered, aad_context=context)
    assert TOKEN not in str(error.value)


def test_store_boots_and_reads_legacy_without_key_but_new_write_fails_closed(tmp_path):
    db_path = tmp_path / "legacy.db"
    legacy_id = uuid4()
    _insert_legacy(db_path, credential_id=legacy_id)

    store = SqliteIntegrationCredentialStore(str(db_path), keyring=CredentialKeyring.disabled())
    rows = store.list(status="all")
    assert rows[0].credentials["access_token"] == TOKEN
    status = store.get_security_status(legacy_id)
    assert status is not None
    assert status.legacy_unverified is True
    assert status.write_eligible is False
    assert store.get_encryption_summary().__dict__ == {
        "total": 1,
        "active": 1,
        "encrypted": 0,
        "legacy_plaintext": 1,
        "decryptable": 1,
        "write_eligible": 0,
        "needs_rotation": 1,
        "unavailable": 0,
    }

    with pytest.raises(HTTPException) as error:
        store.upsert(_credential(provider="google", credentials={"refresh_token": TOKEN}))
    assert error.value.detail["code"] == "credential_encryption_unavailable"
    assert TOKEN not in str(error.value.detail)
    with sqlite_conn(str(db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM integration_credentials WHERE provider='google'"
        ).fetchone()["n"] == 0


def test_new_sqlite_writes_are_encrypted_and_missing_decryption_key_fails_closed(tmp_path):
    db_path = tmp_path / "encrypted.db"
    store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())
    created = store.upsert(_credential())

    raw = _raw_credentials(db_path, created.id)
    assert TOKEN not in raw
    assert "encrypted-integration-credential" in raw
    assert store.list()[0].credentials["access_token"] == TOKEN
    status = store.get_security_status(created.id)
    assert status is not None
    assert status.encrypted and status.decryptable and status.write_eligible
    assert store.get_encryption_summary().write_eligible == 1

    unavailable = SqliteIntegrationCredentialStore(
        str(db_path),
        keyring=CredentialKeyring(keys={"different": _key(9)}, active_key_id="different"),
    )
    missing_status = unavailable.get_security_status(created.id)
    assert missing_status is not None
    assert missing_status.encrypted and not missing_status.decryptable
    assert missing_status.write_eligible is False
    with pytest.raises(HTTPException) as error:
        unavailable.list()
    assert error.value.detail["code"] == "credential_decryption_key_unavailable"
    assert TOKEN not in str(error.value.detail)


def test_rotation_is_dry_run_by_default_atomic_and_preserves_semantic_timestamp(tmp_path):
    db_path = tmp_path / "rotation.db"
    old_store = SqliteIntegrationCredentialStore(
        str(db_path), keyring=_keyring(active="old", old=1)
    )
    old = old_store.upsert(_credential(connection_key="old"))
    legacy_id = uuid4()
    _insert_legacy(db_path, credential_id=legacy_id, connection_key="legacy", token="legacy-token")
    before_old = _raw_credentials(db_path, old.id)
    before_legacy = _raw_credentials(db_path, legacy_id)
    with sqlite_conn(str(db_path)) as conn:
        timestamps_before = {
            row["id"]: row["updated_at"]
            for row in conn.execute("SELECT id, updated_at FROM integration_credentials").fetchall()
        }
        revisions_before = {
            row["id"]: row["semantic_revision"]
            for row in conn.execute("SELECT id, semantic_revision FROM integration_credentials").fetchall()
        }

    rotating = SqliteIntegrationCredentialStore(
        str(db_path), keyring=_keyring(active="current", old=1, current=2)
    )
    plan = rotating.rotate_credentials()
    assert (plan.scanned, plan.candidates, plan.rotated, plan.dry_run) == (2, 2, 0, True)
    assert _raw_credentials(db_path, old.id) == before_old
    assert _raw_credentials(db_path, legacy_id) == before_legacy

    result = rotating.rotate_credentials(dry_run=False)
    assert (result.candidates, result.rotated, result.dry_run) == (2, 2, False)
    assert rotating.list(status="all")[0].credentials
    assert TOKEN not in _raw_credentials(db_path, old.id)
    assert "legacy-token" not in _raw_credentials(db_path, legacy_id)
    assert rotating.get_security_status(old.id).key_id == "current"
    assert rotating.get_security_status(legacy_id).write_eligible is True
    with sqlite_conn(str(db_path)) as conn:
        timestamps_after = {
            row["id"]: row["updated_at"]
            for row in conn.execute("SELECT id, updated_at FROM integration_credentials").fetchall()
        }
        revisions_after = {
            row["id"]: row["semantic_revision"]
            for row in conn.execute("SELECT id, semantic_revision FROM integration_credentials").fetchall()
        }
    assert timestamps_after == timestamps_before
    assert revisions_after == revisions_before


def test_semantic_credential_revision_changes_on_reconnect_and_patch(tmp_path):
    store = SqliteIntegrationCredentialStore(
        str(tmp_path / "semantic-revision.db"),
        keyring=_keyring(active="current", current=3),
    )
    created = store.upsert(_credential())
    assert store.get_security_status(created.id).semantic_revision == 1

    reconnected = store.upsert(_credential())
    assert reconnected.id == created.id
    assert store.get_security_status(created.id).semantic_revision == 2

    store.patch(created.id, IntegrationCredentialPatch(status="archived"))
    assert store.get_security_status(created.id).semantic_revision == 3


def test_one_off_rotation_command_is_dry_run_first_and_reports_only_counts(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "operational-rotation.db"
    legacy_id = uuid4()
    _insert_legacy(db_path, credential_id=legacy_id)
    monkeypatch.setenv(KEYRING_ENV, json.dumps({"primary": _encoded_key(5)}))
    monkeypatch.setenv(ACTIVE_KEY_ID_ENV, "primary")

    assert rotate_credentials_main(["--db-path", str(db_path)]) == 0
    dry_run = capsys.readouterr().out
    assert TOKEN not in dry_run
    assert json.loads(dry_run) == {
        "candidates": 1,
        "dry_run": True,
        "ok": True,
        "rotated": 0,
        "scanned": 1,
        "unchanged": 0,
    }
    assert TOKEN in _raw_credentials(db_path, legacy_id)

    assert rotate_credentials_main(["--db-path", str(db_path), "--apply"]) == 0
    applied = capsys.readouterr().out
    assert TOKEN not in applied
    assert json.loads(applied)["rotated"] == 1
    assert TOKEN not in _raw_credentials(db_path, legacy_id)


def test_rotation_script_dry_run_is_strictly_read_only_on_missing_empty_and_minimal_db(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv(KEYRING_ENV, json.dumps({"primary": _encoded_key(5)}))
    monkeypatch.setenv(ACTIVE_KEY_ID_ENV, "primary")

    missing = tmp_path / "missing-rotation.db"
    assert rotate_credentials_main(["--db-path", str(missing)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "credential_rotation_db_missing"
    assert not missing.exists()

    empty = tmp_path / "empty-rotation.db"
    empty.touch()
    empty_before = _read_only_db_snapshot(empty)
    assert rotate_credentials_main(["--db-path", str(empty)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "candidates": 0,
        "dry_run": True,
        "ok": True,
        "rotated": 0,
        "scanned": 0,
        "unchanged": 0,
    }
    assert _read_only_db_snapshot(empty) == empty_before

    minimal = tmp_path / "minimal-legacy-rotation.db"
    credential_id = uuid4()
    _create_minimal_credential_db(
        minimal,
        credential_id=credential_id,
        credentials_json=json.dumps({"access_token": TOKEN}),
    )
    minimal_before = _read_only_db_snapshot(minimal)
    assert rotate_credentials_main(["--db-path", str(minimal)]) == 0
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert json.loads(output) == {
        "candidates": 1,
        "dry_run": True,
        "ok": True,
        "rotated": 0,
        "scanned": 1,
        "unchanged": 0,
    }
    # mtime, sqlite_master, schema_version, file permission/ACL attributes and
    # credential data are all byte-for-byte/logically unchanged.
    assert _read_only_db_snapshot(minimal) == minimal_before


def test_rotation_script_dry_run_authenticates_corrupt_candidate_without_writes(
    tmp_path, monkeypatch, capsys
):
    credential_id = uuid4()
    old_ring = _keyring(active="old", old=3)
    envelope = old_ring.encrypt_json(
        {"access_token": TOKEN},
        aad_context={
            "credential_id": credential_id,
            "provider": "meta",
            "scope_type": "global",
            "scope_id": None,
            "connection_key": "default",
        },
    )
    envelope["ciphertext"] = str(envelope["ciphertext"])[:-1] + (
        "A" if not str(envelope["ciphertext"]).endswith("A") else "B"
    )
    db_path = tmp_path / "corrupt-rotation-candidate.db"
    _create_minimal_credential_db(
        db_path,
        credential_id=credential_id,
        credentials_json=json.dumps(envelope, separators=(",", ":")),
    )
    monkeypatch.setenv(
        KEYRING_ENV,
        json.dumps({"old": _encoded_key(3), "new": _encoded_key(4)}),
    )
    monkeypatch.setenv(ACTIVE_KEY_ID_ENV, "new")
    before = _read_only_db_snapshot(db_path)

    assert rotate_credentials_main(["--db-path", str(db_path)]) == 2
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert json.loads(output)["code"] == "credential_rotation_integrity_failed"
    assert _read_only_db_snapshot(db_path) == before


def test_rotation_authenticates_every_candidate_before_any_write(tmp_path):
    db_path = tmp_path / "atomic-rotation.db"
    old_store = SqliteIntegrationCredentialStore(
        str(db_path), keyring=_keyring(active="old", old=3)
    )
    first = old_store.upsert(_credential(connection_key="first"))
    second = old_store.upsert(_credential(connection_key="second"))
    tampered = json.loads(_raw_credentials(db_path, second.id))
    tampered["ciphertext"] = str(tampered["ciphertext"])[:-1] + (
        "A" if not str(tampered["ciphertext"]).endswith("A") else "B"
    )
    with sqlite_conn(str(db_path)) as conn:
        conn.execute(
            "UPDATE integration_credentials SET credentials_json=? WHERE id=?",
            (json.dumps(tampered, separators=(",", ":")), str(second.id)),
        )
        conn.commit()
    before = {
        str(first.id): _raw_credentials(db_path, first.id),
        str(second.id): _raw_credentials(db_path, second.id),
    }

    rotating = SqliteIntegrationCredentialStore(
        str(db_path), keyring=_keyring(active="new", old=3, new=4)
    )
    with pytest.raises(HTTPException) as error:
        rotating.rotate_credentials(dry_run=False)
    assert error.value.detail["code"] == "credential_rotation_integrity_failed"
    assert TOKEN not in str(error.value.detail)
    assert _raw_credentials(db_path, first.id) == before[str(first.id)]
    assert _raw_credentials(db_path, second.id) == before[str(second.id)]


def test_money_write_binding_is_explicit_server_owned_and_exact(tmp_path):
    db_path = tmp_path / "bindings.db"
    clients = SqliteClientStore(str(db_path))
    accounts = SqliteAdAccountStore(str(db_path), clients)
    tenant = clients.create(ClientCreate(name="Bound tenant"))
    credential_store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())
    credential = credential_store.upsert(
        _credential(scope_type="client", scope_id=tenant.id)
    )
    account = accounts.create(
        AdAccountCreate(
            client_id=tenant.id,
            platform="meta",
            external_account_id="123456",
            name="Meta account",
            metadata={"integration_credential_id": str(credential.id)},
        )
    )

    # Historical/mutable metadata is never accepted as a money-write binding.
    assert credential_store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="123456",
    ) is None
    binding = credential_store.bind_provider_account(
        ad_account_id=account.id,
        provider="facebook",
        external_account_id="act_123456",
        credential_id=credential.id,
        bound_by=None,
    )
    assert binding.credential_id == credential.id
    assert binding.client_id == tenant.id
    resolved = credential_store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="act_123456",
    )
    assert resolved is not None
    assert resolved.binding.credential_id == credential.id
    assert resolved.credential.credentials["access_token"] == TOKEN
    assert resolved.security.write_eligible is True
    assert credential_store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="999999",
    ) is None

    # Reconnecting/replacing a credential never inherits the old binding.
    replaced = credential_store.upsert(
        _credential(
            scope_type="client",
            scope_id=tenant.id,
            credentials={"access_token": "replacement-token"},
        )
    )
    assert replaced.id == credential.id
    assert credential_store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="123456",
    ) is None


def test_legacy_duplicate_active_meta_assignment_invalidates_money_write_binding(tmp_path):
    db_path = tmp_path / "binding-legacy-duplicate.db"
    clients = SqliteClientStore(str(db_path))
    accounts = SqliteAdAccountStore(str(db_path), clients)
    first = clients.create(ClientCreate(name="First owner"))
    second = clients.create(ClientCreate(name="Legacy duplicate owner"))
    account = accounts.create(
        AdAccountCreate(
            client_id=first.id,
            platform="meta",
            external_account_id="123456",
            name="Original Meta account",
        )
    )
    store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())
    credential = store.upsert(_credential(scope_type="client", scope_id=first.id))
    store.bind_provider_account(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="123456",
        credential_id=credential.id,
        bound_by=None,
    )

    # Simulate a database created before canonical assignment guards existed.
    duplicate_id = uuid4()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with sqlite_conn(str(db_path)) as conn:
        conn.execute("DROP TRIGGER trg_ad_accounts_active_assignment_insert")
        conn.execute(
            """
            INSERT INTO ad_accounts
              (id, client_id, platform, external_account_id, name, currency, status, created_at, updated_at)
            VALUES (?, ?, 'facebook', 'act_123456', 'Legacy duplicate', 'USD', 'active', ?, ?)
            """,
            (str(duplicate_id), str(second.id), now, now),
        )
        conn.commit()

    assert store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="123456",
    ) is None
    with pytest.raises(HTTPException) as exc_info:
        store.bind_provider_account(
            ad_account_id=account.id,
            provider="meta",
            external_account_id="123456",
            credential_id=credential.id,
            bound_by=None,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "credential_binding_assignment_conflict"


def test_agency_hard_delete_without_budget_history_removes_provider_binding_first(tmp_path):
    db_path = tmp_path / "agency-binding-cleanup.db"
    clients = SqliteClientStore(str(db_path))
    accounts = SqliteAdAccountStore(str(db_path), clients)
    tenant = clients.create(ClientCreate(name="Agency-bound tenant"))
    account = accounts.create(
        AdAccountCreate(
            client_id=tenant.id,
            platform="meta",
            external_account_id="987654",
            name="Agency Meta account",
        )
    )
    agency_id = uuid4()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with sqlite_conn(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO agencies (id,name,slug,status,plan,notes,created_at,updated_at)
            VALUES (?,?,?,'active','starter',NULL,?,?)
            """,
            (str(agency_id), "Disposable agency", f"delete-{agency_id.hex[:8]}", now, now),
        )
        conn.execute(
            """
            INSERT INTO agency_client_access (id,agency_id,client_id,created_at,updated_at)
            VALUES (?,?,?,?,?)
            """,
            (str(uuid4()), str(agency_id), str(tenant.id), now, now),
        )
        conn.commit()
    credential_store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())
    credential = credential_store.upsert(
        _credential(scope_type="agency", scope_id=agency_id, connection_key="agency-delete")
    )
    credential_store.bind_provider_account(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="987654",
        credential_id=credential.id,
        bound_by=None,
    )

    SqlitePlatformAdminStore(str(db_path), SqliteAuthStore(str(db_path))).delete_agency(agency_id)
    with sqlite_conn(str(db_path)) as conn:
        assert conn.execute("SELECT 1 FROM agencies WHERE id=?", (str(agency_id),)).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM integration_credentials WHERE id=?", (str(credential.id),)
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM provider_account_credential_bindings WHERE credential_id=?",
            (str(credential.id),),
        ).fetchone() is None
        assert conn.execute("SELECT 1 FROM ad_accounts WHERE id=?", (str(account.id),)).fetchone()


def test_binding_rejects_foreign_client_credential_and_stales_on_account_change(tmp_path):
    db_path = tmp_path / "binding-scope.db"
    clients = SqliteClientStore(str(db_path))
    accounts = SqliteAdAccountStore(str(db_path), clients)
    first = clients.create(ClientCreate(name="First"))
    second = clients.create(ClientCreate(name="Second"))
    account = accounts.create(
        AdAccountCreate(
            client_id=first.id,
            platform="meta",
            external_account_id="100",
            name="First Meta",
        )
    )
    store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())
    foreign = store.upsert(
        _credential(scope_type="client", scope_id=second.id, connection_key="foreign")
    )
    with pytest.raises(HTTPException) as error:
        store.bind_provider_account(
            ad_account_id=account.id,
            provider="meta",
            external_account_id="100",
            credential_id=foreign.id,
            bound_by=None,
        )
    assert error.value.detail["code"] == "credential_binding_scope_mismatch"

    own = store.upsert(_credential(scope_type="client", scope_id=first.id, connection_key="own"))
    store.bind_provider_account(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="100",
        credential_id=own.id,
        bound_by=None,
    )
    with sqlite_conn(str(db_path)) as conn:
        conn.execute(
            "UPDATE ad_accounts SET external_account_id='101' WHERE id=?",
            (str(account.id),),
        )
        conn.commit()
    assert store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="100",
    ) is None
    assert store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="101",
    ) is None


def test_global_credential_binding_stales_when_account_moves_clients(tmp_path):
    db_path = tmp_path / "binding-client-move.db"
    clients = SqliteClientStore(str(db_path))
    accounts = SqliteAdAccountStore(str(db_path), clients)
    original = clients.create(ClientCreate(name="Original"))
    replacement = clients.create(ClientCreate(name="Replacement"))
    account = accounts.create(
        AdAccountCreate(
            client_id=original.id,
            platform="meta",
            external_account_id="200",
            name="Global Meta",
        )
    )
    store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())
    credential = store.upsert(_credential(scope_type="global", connection_key="global"))
    store.bind_provider_account(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="200",
        credential_id=credential.id,
        bound_by=None,
    )
    assert store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="200",
    ) is not None

    with sqlite_conn(str(db_path)) as conn:
        conn.execute(
            "UPDATE ad_accounts SET client_id=? WHERE id=?",
            (str(replacement.id), str(account.id)),
        )
        conn.commit()
    assert store.resolve_bound_credential(
        ad_account_id=account.id,
        provider="meta",
        external_account_id="200",
    ) is None
