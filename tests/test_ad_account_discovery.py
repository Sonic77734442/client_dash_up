from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from uuid import UUID, uuid4

from app.main import app
from app.services.providers import meta, tiktok


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-discovery@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    client.headers.update({"Authorization": f"Bearer {issued.json()['token']}"})


def mk_client(name: str):
    res = client.post("/clients", json={"name": name, "status": "active", "default_currency": "USD"})
    assert res.status_code == 200
    return res.json()


def mk_account(client_id: str, platform: str, external: str, name: str, status: str = "active"):
    res = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external,
            "name": name,
            "currency": "USD",
            "status": status,
        },
    )
    assert res.status_code == 200
    return res.json()


def test_discover_uses_discovery_inbox_when_client_id_omitted_for_multiple_clients():
    reset_state()
    c1 = mk_client("Acme")
    c2 = mk_client("Nova")

    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "m-1", "name": "Meta One", "currency": "USD"}]
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta"})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 1
    assert body["client_id"] not in {c1["id"], c2["id"]}

    inbox = client.get(f"/clients/{body['client_id']}")
    assert inbox.status_code == 200
    assert inbox.json()["name"] == "Discovery Inbox"
    assert str(inbox.json().get("notes") or "").startswith("system:discovery_inbox:")


def test_discover_uses_single_client_when_client_id_not_provided():
    reset_state()
    c = mk_client("Acme")
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "m-1", "name": "Meta One", "currency": "USD"}]
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta"})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 1
    assert body["client_id"] == c["id"]


def test_discover_creates_accounts_for_selected_client():
    reset_state()
    c = mk_client("Acme")
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "m-1", "name": "Meta One", "currency": "USD"}]
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta", "client_id": c["id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["providers_failed"] == {}
    assert body["items"][0]["platform"] == "meta"
    assert body["items"][0]["client_id"] == c["id"]
    assert body["items"][0]["external_account_id"] == "m-1"


def test_discover_updates_existing_account_when_upsert_enabled():
    reset_state()
    c = mk_client("Acme")
    existing = mk_account(c["id"], "google", "1234567890", "Old Name", status="archived")
    app.state.ad_account_discovery_service.discoverers = {
        "google": lambda: [{"external_account_id": "1234567890", "name": "New Name", "currency": "USD"}]
    }

    res = client.post(
        "/ad-accounts/discover",
        json={"provider": "google", "client_id": c["id"], "upsert_existing": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    updated = body["items"][0]
    assert updated["id"] == existing["id"]
    assert updated["name"] == "New Name"
    assert updated["currency"] == "USD"
    assert updated["status"] == "active"


def test_discover_skips_accounts_with_currency_that_does_not_match_target_client():
    reset_state()
    c = mk_client("USD tenant")
    app.state.ad_account_discovery_service.discoverers = {
        "google": lambda: [{"external_account_id": "eur-1", "name": "EUR account", "currency": "EUR"}]
    }

    res = client.post(
        "/ad-accounts/discover",
        json={"provider": "google", "client_id": c["id"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["discovered"] == 1
    assert body["created"] == 0
    assert body["skipped"] == 1
    assert body["providers_failed"]["google"] == "currency_mismatch_skipped:1"


def test_discover_returns_provider_failures_without_crashing():
    reset_state()
    c = mk_client("Acme")
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: (_ for _ in ()).throw(RuntimeError("meta unavailable")),
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta", "client_id": c["id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 0
    assert body["updated"] == 0
    assert body["providers_failed"]["meta"] == "meta unavailable"


@pytest.mark.parametrize(
    ("provider", "provider_module", "credentials"),
    [
        ("meta", meta, {"access_token": "tenant-meta-token"}),
        ("tiktok", tiktok, {"access_token": "tenant-tiktok-token"}),
    ],
)
def test_discover_surfaces_scoped_provider_auth_errors(monkeypatch, provider, provider_module, credentials):
    reset_state()
    target = mk_client(f"{provider} scoped failure")
    service = app.state.ad_account_discovery_service
    service.discoverers = {
        provider: service._discover_meta_accounts if provider == "meta" else service._discover_tiktok_accounts
    }
    service.credential_candidates_resolver = lambda *_args: [credentials]

    def fail(_credentials=None):
        raise HTTPException(status_code=502, detail={"code": "provider_auth_failed", "message": "Provider authorization failed"})

    monkeypatch.setattr(provider_module, "list_accounts", fail)
    run = client.post("/ad-accounts/discover", json={"provider": provider, "client_id": target["id"]})

    assert run.status_code == 200
    assert run.json()["created"] == 0
    assert run.json()["providers_failed"] == {provider: "Provider authorization failed"}


def test_discovery_preserves_none_vs_empty_scoped_credentials():
    reset_state()
    target = mk_client("Credential sentinel discovery")
    service = app.state.ad_account_discovery_service
    seen = []
    service.discoverers = {"meta": lambda credentials=None: seen.append(credentials) or []}

    service.credential_candidates_resolver = lambda *_args: []
    first = client.post("/ad-accounts/discover", json={"provider": "meta", "client_id": target["id"]})
    assert first.status_code == 200

    service.credential_candidates_resolver = lambda *_args: [{}]
    second = client.post("/ad-accounts/discover", json={"provider": "meta", "client_id": target["id"]})
    assert second.status_code == 200

    assert seen == [None, {}]


@pytest.mark.parametrize("resolved_candidates", ([], [None]))
def test_agency_discovery_without_scoped_credentials_never_uses_global_env(
    monkeypatch,
    resolved_candidates,
):
    reset_state()
    target = mk_client("No agency discovery credentials")
    service = app.state.ad_account_discovery_service
    calls = []
    monkeypatch.setenv("META_ACCESS_TOKEN", "platform-global-token-must-not-be-used")
    monkeypatch.setenv("META_ACCOUNT_IDS", "platform-global-account")
    service.credential_candidates_resolver = lambda *_args: resolved_candidates
    service.discoverers = {
        "meta": lambda credentials=None: calls.append(credentials)
        or [{"external_account_id": "platform-global-account", "currency": "USD"}],
    }

    result = service.discover(
        provider="meta",
        client_id=UUID(target["id"]),
        user_id=uuid4(),
        agency_id=uuid4(),
    )

    assert calls == []
    assert result.created == 0
    assert result.providers_failed == {
        "meta": "Provider credentials are missing or incomplete."
    }


def test_second_active_external_account_is_blocked_across_clients():
    reset_state()
    c1 = mk_client("Acme")
    c2 = mk_client("Nova")

    a1 = mk_account(c1["id"], "meta", "same-ext", "A1")
    duplicate = client.post(
        "/ad-accounts",
        json={
            "client_id": c2["id"],
            "platform": "meta",
            "external_account_id": "same-ext",
            "name": "A2",
            "currency": "USD",
            "status": "active",
        },
    )

    assert a1["client_id"] == c1["id"]
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "assignment_conflict"
    assert "assigned_client_id" not in duplicate.json()["error"]["details"]


def test_discover_does_not_reassign_account_between_clients():
    reset_state()
    c1 = mk_client("Acme")
    c2 = mk_client("Nova")
    existing = mk_account(c1["id"], "google", "1234567890", "Acme Google")

    app.state.ad_account_discovery_service.discoverers = {
        "google": lambda: [{"external_account_id": "1234567890", "name": "Nova Google", "currency": "USD"}]
    }

    run = client.post("/ad-accounts/discover", json={"provider": "google", "client_id": c2["id"], "upsert_existing": True})
    assert run.status_code == 200
    body = run.json()
    assert body["created"] == 0
    assert body["updated"] == 0
    assert body["skipped"] == 1
    assert body["providers_failed"]["google"] == "assignment_conflict:1"

    list_c1 = client.get(f"/ad-accounts?client_id={c1['id']}&status=all")
    assert list_c1.status_code == 200
    rows_c1 = list_c1.json()["items"]
    assert any(x["id"] == existing["id"] and x["client_id"] == c1["id"] for x in rows_c1)

    list_c2 = client.get(f"/ad-accounts?client_id={c2['id']}&status=all")
    assert list_c2.status_code == 200
    rows_c2 = list_c2.json()["items"]
    assert len([x for x in rows_c2 if x["platform"] == "google" and x["external_account_id"] == "1234567890"]) == 0


def test_discover_can_assign_account_after_previous_parent_client_is_archived():
    reset_state()
    archived_client = mk_client("Archived owner")
    active_client = mk_client("New owner")
    old_account = mk_account(archived_client["id"], "meta", "act_778899", "Old Meta")

    archived = client.delete(f"/clients/{archived_client['id']}")
    assert archived.status_code == 200

    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "778899", "name": "New Meta", "currency": "USD"}]
    }
    run = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "client_id": active_client["id"], "upsert_existing": True},
    )

    assert run.status_code == 200
    body = run.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["skipped"] == 0
    assert body["providers_failed"] == {}
    assert body["items"][0]["client_id"] == active_client["id"]
    assert body["items"][0]["external_account_id"] == "778899"
    assert body["items"][0]["id"] != old_account["id"]


def test_direct_account_writes_use_provider_canonical_identity_for_conflicts():
    reset_state()
    c1 = mk_client("Canonical A")
    c2 = mk_client("Canonical B")

    meta = mk_account(c1["id"], "facebook", "act_123456", "Meta Canonical")
    assert meta["platform"] == "meta"
    assert meta["external_account_id"] == "123456"
    meta_conflict = client.post(
        "/ad-accounts",
        json={
            "client_id": c2["id"],
            "platform": "meta",
            "external_account_id": "123456",
            "name": "Meta Duplicate",
            "currency": "USD",
            "status": "active",
        },
    )
    assert meta_conflict.status_code == 409
    assert meta_conflict.json()["error"]["code"] == "assignment_conflict"

    google = mk_account(c1["id"], "google", "123-456-7890", "Google Canonical")
    assert google["external_account_id"] == "1234567890"
    google_conflict = client.post(
        "/ad-accounts",
        json={
            "client_id": c2["id"],
            "platform": "google",
            "external_account_id": "123 456 7890",
            "name": "Google Duplicate",
            "currency": "USD",
            "status": "active",
        },
    )
    assert google_conflict.status_code == 409
    assert google_conflict.json()["error"]["code"] == "assignment_conflict"
