from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import IntegrationCredentialCreate


client = TestClient(app)


def _reset():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)

    admin = client.post(
        "/auth/internal/users",
        json={
            "email": "admin@solo-security.local",
            "name": "Admin",
            "role": "admin",
            "status": "active",
        },
    )
    assert admin.status_code == 200
    token = _issue_token(admin.json()["id"])
    return admin.json(), token


def _issue_token(user_id: str) -> str:
    issued = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": user_id, "ttl_minutes": 60},
    )
    assert issued.status_code == 200, issued.text
    return issued.json()["token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_client(name: str, admin_token: str) -> dict:
    response = client.post(
        "/clients",
        json={"name": name, "status": "active", "default_currency": "USD"},
        headers=_headers(admin_token),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_account(client_id: str, external_id: str, admin_token: str) -> dict:
    response = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": "meta",
            "external_account_id": external_id,
            "name": f"Meta {external_id}",
            "currency": "USD",
            "status": "active",
        },
        headers=_headers(admin_token),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_user(role: str, email: str) -> dict:
    response = client.post(
        "/auth/internal/users",
        json={"email": email, "name": role, "role": role, "status": "active"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assign(user_id: str, client_id: str, role: str = "client"):
    return client.post(
        "/auth/internal/access",
        json={"user_id": user_id, "client_id": client_id, "role": role},
    )


def _provision_solo(admin_token: str, *, email: str = "owner@solo-security.local"):
    tenant = _create_client("Solo tenant", admin_token)
    user = _create_user("solo_client", email)
    assigned = _assign(user["id"], tenant["id"])
    assert assigned.status_code == 200, assigned.text
    return user, tenant, _issue_token(user["id"])


def _budget_payload(client_id: str, *, account_id: str | None = None) -> dict:
    return {
        "client_id": client_id,
        "scope": "account" if account_id else "client",
        "account_id": account_id,
        "amount": "1000.00",
        "currency": "USD",
        "period_type": "monthly",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "note": "solo security test",
    }


def test_viewer_remains_read_only_and_solo_cannot_mutate_registry_or_raw_stats():
    _admin, admin_token = _reset()
    solo, own_client, solo_token = _provision_solo(admin_token)
    own_account = _create_account(own_client["id"], "solo-guard", admin_token)

    viewer = _create_user("client", "viewer@solo-security.local")
    assert _assign(viewer["id"], own_client["id"]).status_code == 200
    viewer_token = _issue_token(viewer["id"])

    viewer_requests = [
        client.get(
            f"/auth/facebook/start?intent=connect&client_id={own_client['id']}&next=/integrations",
            headers=_headers(viewer_token),
            follow_redirects=False,
        ),
        client.post(
            "/ad-accounts/discover",
            json={"provider": "meta", "client_id": own_client["id"]},
            headers=_headers(viewer_token),
        ),
        client.post(
            "/ad-accounts/sync/run",
            json={"client_id": own_client["id"], "account_ids": [own_account["id"]]},
            headers=_headers(viewer_token),
        ),
        client.post(
            "/budgets",
            json=_budget_payload(own_client["id"]),
            headers=_headers(viewer_token),
        ),
    ]
    assert [response.status_code for response in viewer_requests] == [403, 403, 403, 403]

    forbidden_solo_mutations = [
        client.post(
            "/clients",
            json={"name": "Forbidden", "status": "active", "default_currency": "USD"},
            headers=_headers(solo_token),
        ),
        client.patch(
            f"/clients/{own_client['id']}",
            json={"name": "Forbidden rename"},
            headers=_headers(solo_token),
        ),
        client.delete(f"/clients/{own_client['id']}", headers=_headers(solo_token)),
        client.patch(
            f"/ad-accounts/{own_account['id']}",
            json={"name": "Forbidden account rename"},
            headers=_headers(solo_token),
        ),
        client.delete(f"/ad-accounts/{own_account['id']}", headers=_headers(solo_token)),
        client.post(
            "/ad-stats/ingest",
            json={
                "rows": [
                    {
                        "ad_account_id": own_account["id"],
                        "date": "2026-08-01",
                        "platform": "meta",
                        "impressions": 1,
                        "clicks": 0,
                        "spend": "1.00",
                        "conversions": "0.00",
                    }
                ]
            },
            headers=_headers(solo_token),
        ),
    ]
    assert all(response.status_code == 403 for response in forbidden_solo_mutations)
    assert solo["role"] == "solo_client"


def test_solo_sync_rejects_omitted_unknown_and_mixed_scope_before_creating_jobs():
    _admin, admin_token = _reset()
    _solo, own_client, solo_token = _provision_solo(admin_token)
    foreign_client = _create_client("Foreign tenant", admin_token)
    own_account = _create_account(own_client["id"], "solo-own", admin_token)
    foreign_account = _create_account(foreign_client["id"], "solo-foreign", admin_token)

    omitted = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [own_account["id"]]},
        headers=_headers(solo_token),
    )
    unknown = client.post(
        "/ad-accounts/sync/run",
        json={"client_id": own_client["id"], "account_ids": [str(uuid4())]},
        headers=_headers(solo_token),
    )
    mixed = client.post(
        "/ad-accounts/sync/run",
        json={
            "client_id": own_client["id"],
            "account_ids": [own_account["id"], foreign_account["id"]],
        },
        headers=_headers(solo_token),
    )

    assert omitted.status_code == 403
    assert unknown.status_code == 404
    assert mixed.status_code == 403
    jobs = client.get("/ad-accounts/sync/jobs", headers=_headers(solo_token))
    assert jobs.status_code == 200
    assert jobs.json()["count"] == 0


def test_solo_credentials_are_owner_only_for_listing_archiving_and_discovery():
    admin, admin_token = _reset()
    solo, own_client, solo_token = _provision_solo(admin_token)

    admin_credential = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "meta",
            "scope_type": "client",
            "scope_id": own_client["id"],
            "connection_key": "admin-owned",
            "credentials": {"access_token": "admin-secret"},
            "created_by": admin["id"],
        },
        headers=_headers(admin_token),
    )
    assert admin_credential.status_code == 200, admin_credential.text
    assert client.post(
        "/platform/integration-credentials",
        json={
            "provider": "meta",
            "scope_type": "global",
            "connection_key": "global-owned",
            "credentials": {"access_token": "global-secret"},
            "created_by": admin["id"],
        },
        headers=_headers(admin_token),
    ).status_code == 200

    calls: list[dict | None] = []

    def fake_discoverer(credentials=None):
        calls.append(credentials)
        return [{"external_account_id": "own-discovered", "name": "Own", "currency": "USD"}]

    app.state.ad_account_discovery_service.discoverers = {"meta": fake_discoverer}

    missing_own = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "client_id": own_client["id"]},
        headers=_headers(solo_token),
    )
    assert missing_own.status_code == 200
    assert calls == []
    assert missing_own.json()["providers_failed"]["meta"]

    own_credential = app.state.integration_credential_store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=UUID(own_client["id"]),
            connection_key=f"solo:{solo['id']}:meta:owner",
            credentials={"access_token": "solo-secret"},
            created_by=UUID(solo["id"]),
        )
    )

    listed = client.get("/me/integration-connections?status=all", headers=_headers(solo_token))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["items"]] == [str(own_credential.id)]

    denied_archive = client.delete(
        f"/me/integration-connections/{admin_credential.json()['id']}",
        headers=_headers(solo_token),
    )
    assert denied_archive.status_code == 403

    discovered = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "client_id": own_client["id"]},
        headers=_headers(solo_token),
    )
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["created"] == 1
    assert calls == [{"access_token": "solo-secret"}]


def test_solo_budget_writes_are_limited_to_the_owned_client():
    _admin, admin_token = _reset()
    _solo, own_client, solo_token = _provision_solo(admin_token)
    foreign_client = _create_client("Foreign budget tenant", admin_token)

    own = client.post(
        "/budgets",
        json=_budget_payload(own_client["id"]),
        headers=_headers(solo_token),
    )
    assert own.status_code == 200, own.text

    foreign_create = client.post(
        "/budgets",
        json=_budget_payload(foreign_client["id"]),
        headers=_headers(solo_token),
    )
    assert foreign_create.status_code == 403

    foreign = client.post(
        "/budgets",
        json=_budget_payload(foreign_client["id"]),
        headers=_headers(admin_token),
    )
    assert foreign.status_code == 200, foreign.text

    assert client.patch(
        f"/budgets/{foreign.json()['id']}",
        json={"amount": "900.00"},
        headers=_headers(solo_token),
    ).status_code == 403
    assert client.delete(
        f"/budgets/{foreign.json()['id']}",
        headers=_headers(solo_token),
    ).status_code == 403

    own_patch = client.patch(
        f"/budgets/{own.json()['id']}",
        json={"amount": "1100.00"},
        headers=_headers(solo_token),
    )
    assert own_patch.status_code == 200, own_patch.text


def test_solo_provisioning_guards_and_legacy_multi_scope_session_fail_closed():
    _admin, admin_token = _reset()
    first = _create_client("First", admin_token)
    second = _create_client("Second", admin_token)

    solo = _create_user("solo_client", "guarded-owner@solo-security.local")
    assert _assign(solo["id"], first["id"], "agency").status_code == 409
    assert _assign(solo["id"], first["id"], "client").status_code == 200
    assert _assign(solo["id"], second["id"], "client").status_code == 409

    legacy = _create_user("client", "legacy-owner@solo-security.local")
    assert _assign(legacy["id"], first["id"]).status_code == 200
    assert _assign(legacy["id"], second["id"]).status_code == 200
    legacy_id = UUID(legacy["id"])
    app.state.auth_store.users[legacy_id] = app.state.auth_store.users[legacy_id].model_copy(
        update={"role": "solo_client"}
    )
    legacy_token = _issue_token(legacy["id"])

    me = client.get("/auth/me", headers=_headers(legacy_token))
    assert me.status_code == 200
    assert me.json()["session"]["role"] == "solo_client"
    assert me.json()["session"]["accessible_client_ids"] == []

    capability = client.get("/me/integration-connections", headers=_headers(legacy_token))
    assert capability.status_code == 409
    assert capability.json()["error"]["code"] in {"solo_owner_scope_invalid", "solo_owner_scope_stale"}
