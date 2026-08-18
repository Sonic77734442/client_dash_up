from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdAccountPatch


client = TestClient(app)


def _reset_state() -> None:
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={
            "email": "admin-account-metadata@test.local",
            "name": "Admin",
            "role": "admin",
            "status": "active",
        },
    )
    assert admin.status_code == 200
    session = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": admin.json()["id"], "ttl_minutes": 60},
    )
    assert session.status_code == 200
    client.headers.update({"Authorization": f"Bearer {session.json()['token']}"})


def _create_client(name: str) -> dict:
    response = client.post(
        "/clients",
        json={"name": name, "status": "active", "default_currency": "USD"},
    )
    assert response.status_code == 200
    return response.json()


def _create_account(client_id: str, *, metadata=None) -> dict:
    payload = {
        "client_id": client_id,
        "platform": "meta",
        "external_account_id": "123456789",
        "name": "Meta account",
        "currency": "USD",
        "status": "active",
    }
    if metadata is not None:
        payload["metadata"] = metadata
    response = client.post("/ad-accounts", json=payload)
    assert response.status_code == 200
    return response.json()


def _seed_metadata(account_id: str, metadata: dict) -> None:
    app.state.ad_account_store.patch(
        UUID(account_id),
        AdAccountPatch(metadata=metadata),
    )


def test_create_rejects_forged_server_managed_metadata() -> None:
    _reset_state()
    tenant = _create_client("Metadata create tenant")

    response = client.post(
        "/ad-accounts",
        json={
            "client_id": tenant["id"],
            "platform": "meta",
            "external_account_id": "forged-create",
            "name": "Forged",
            "currency": "USD",
            "metadata": {
                "integration_credential_id": str(uuid4()),
                "sync_status": "success",
                "last_sync_at": "2026-08-18T00:00:00",
            },
        },
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "ad_account_metadata_reserved"
    assert error["details"]["keys"] == [
        "integration_credential_id",
        "last_sync_at",
        "sync_status",
    ]
    assert client.get(f"/ad-accounts?client_id={tenant['id']}&status=all").json()["count"] == 0


def test_patch_rejects_forgery_and_preserves_server_metadata_on_user_updates() -> None:
    _reset_state()
    tenant = _create_client("Metadata patch tenant")
    account = _create_account(tenant["id"])
    credential_id = str(uuid4())
    server_metadata = {
        "integration_credential_id": credential_id,
        "meta_connection_status": "ready",
        "last_sync_at": "2026-08-17T12:00:00",
        "sync_status": "success",
        "customer_note": "old",
    }
    _seed_metadata(account["id"], server_metadata)

    forged = client.patch(
        f"/ad-accounts/{account['id']}",
        json={
            "metadata": {
                "meta_connection_status": "ready",
                "last_sync_at": "2099-01-01T00:00:00",
            }
        },
    )
    assert forged.status_code == 400
    assert forged.json()["error"]["code"] == "ad_account_metadata_reserved"
    unchanged = client.get(f"/ad-accounts/{account['id']}").json()
    assert unchanged["metadata"] == server_metadata

    allowed = client.patch(
        f"/ad-accounts/{account['id']}",
        json={"metadata": {"customer_note": "new"}},
    )
    assert allowed.status_code == 200
    allowed_metadata = allowed.json()["metadata"]
    assert allowed_metadata["customer_note"] == "new"
    assert allowed_metadata["integration_credential_id"] == credential_id
    assert allowed_metadata["meta_connection_status"] == "ready"
    assert allowed_metadata["last_sync_at"] == "2026-08-17T12:00:00"
    assert allowed_metadata["sync_status"] == "success"

    cleared = client.patch(f"/ad-accounts/{account['id']}", json={"metadata": None})
    assert cleared.status_code == 200
    cleared_metadata = cleared.json()["metadata"]
    assert "customer_note" not in cleared_metadata
    assert cleared_metadata["integration_credential_id"] == credential_id
    assert cleared_metadata["last_sync_at"] == "2026-08-17T12:00:00"


def test_moving_meta_account_clears_pin_blocks_sync_until_rediscovery() -> None:
    _reset_state()
    source = _create_client("Meta source tenant")
    target = _create_client("Meta target tenant")
    account = _create_account(source["id"])
    old_credential_id = str(uuid4())
    _seed_metadata(
        account["id"],
        {
            "integration_credential_id": old_credential_id,
            "meta_connection_status": "ready",
            "meta_connection_diagnostic_code": "meta_connection_ready",
            "customer_note": "keep me",
        },
    )

    moved = client.patch(
        f"/ad-accounts/{account['id']}",
        json={"client_id": target["id"]},
    )
    assert moved.status_code == 200
    moved_metadata = moved.json()["metadata"]
    assert "integration_credential_id" not in moved_metadata
    assert moved_metadata["meta_rediscovery_required"] is True
    assert moved_metadata["meta_connection_status"] == "error"
    assert (
        moved_metadata["meta_connection_diagnostic_code"]
        == "meta_client_changed_rediscovery_required"
    )
    assert moved_metadata["customer_note"] == "keep me"

    fetch_calls = []
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda *_args, **_kwargs: fetch_calls.append(True) or [],
    }
    sync = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [account["id"]], "force": True},
    )
    assert sync.status_code == 200
    assert sync.json()["failed"] == 1
    assert sync.json()["jobs"][0]["error_code"] == "provider_reconnect_required"
    assert fetch_calls == []
    after_failed_sync = client.get(f"/ad-accounts/{account['id']}").json()["metadata"]
    assert after_failed_sync["meta_connection_diagnostic_code"] == "meta_rediscovery_required"

    new_credential_id = str(uuid4())
    app.state.ad_account_discovery_service.credential_candidates_resolver = (
        lambda *_args: [
            {
                "access_token": "tenant-meta-token",
                "__credential_id": new_credential_id,
                "__scope_type": "client",
                "__scope_id": target["id"],
            }
        ]
    )
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda _credentials: [
            {
                "external_account_id": "123456789",
                "name": "Meta account",
                "currency": "USD",
            }
        ]
    }
    rediscovered = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "client_id": target["id"], "upsert_existing": True},
    )
    assert rediscovered.status_code == 200
    assert rediscovered.json()["updated"] == 1
    rebound_metadata = rediscovered.json()["items"][0]["metadata"]
    assert rebound_metadata["meta_rediscovery_required"] is False
    assert rebound_metadata["integration_credential_id"] == new_credential_id
