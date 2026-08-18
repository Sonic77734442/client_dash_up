from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import app


api = TestClient(app)


class _GoogleStartAdapter:
    provider = "google"

    def build_authorize_url(self, cfg, state: str) -> str:
        return f"https://provider.test/google/authorize?state={state}&client_id={cfg.client_id}"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _reset() -> None:
    assert api.post("/_testing/use-inmemory-stores").status_code == 200
    api.headers.pop("Authorization", None)
    api.cookies.clear()


def _create_user(email: str, role: str, *, admin_token: str | None = None) -> dict:
    response = api.post(
        "/auth/internal/users",
        json={"email": email, "name": email.split("@")[0], "role": role, "status": "active"},
        headers=_auth(admin_token) if admin_token else None,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _issue_token(user_id: str, *, admin_token: str | None = None) -> str:
    response = api.post(
        "/auth/internal/sessions/issue",
        json={"user_id": user_id, "ttl_minutes": 60},
        headers=_auth(admin_token) if admin_token else None,
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _bootstrap(*, actor_role: str) -> dict:
    _reset()
    admin = _create_user("security-admin@test.local", "admin")
    admin_token = _issue_token(admin["id"])
    tenant_response = api.post(
        "/clients",
        json={"name": "Agency security tenant", "status": "active", "default_currency": "USD"},
        headers=_auth(admin_token),
    )
    assert tenant_response.status_code == 200, tenant_response.text
    agency_response = api.post(
        "/platform/agencies",
        json={"name": "Agency security boundary", "status": "active", "plan": "starter"},
        headers=_auth(admin_token),
    )
    assert agency_response.status_code == 200, agency_response.text
    agency = agency_response.json()

    actor = _create_user(f"security-{actor_role}@test.local", "agency", admin_token=admin_token)
    actor_member_response = api.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": actor["id"], "role": actor_role, "status": "active"},
        headers=_auth(admin_token),
    )
    assert actor_member_response.status_code == 200, actor_member_response.text

    # Keep every fixture agency governable even when the actor under test is not an owner.
    if actor_role != "owner":
        owner = _create_user("security-owner@test.local", "agency", admin_token=admin_token)
        owner_member_response = api.post(
            f"/platform/agencies/{agency['id']}/members",
            json={"user_id": owner["id"], "role": "owner", "status": "active"},
            headers=_auth(admin_token),
        )
        assert owner_member_response.status_code == 200, owner_member_response.text

    actor_token = _issue_token(actor["id"], admin_token=admin_token)
    return {
        "admin": admin,
        "admin_token": admin_token,
        "actor": actor,
        "actor_member": actor_member_response.json(),
        "actor_token": actor_token,
        "agency": agency,
        "tenant": tenant_response.json(),
    }


def _assign_tenant(state: dict) -> dict:
    response = api.post(
        f"/platform/agencies/{state['agency']['id']}/clients",
        json={"client_id": state["tenant"]["id"]},
        headers=_auth(state["admin_token"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_agency_credential(state: dict, *, provider: str = "meta", connection_key: str = "shared") -> dict:
    credentials = {"access_token": "tenant-token"} if provider == "meta" else {"refresh_token": "tenant-token"}
    response = api.post(
        "/platform/integration-credentials",
        json={
            "provider": provider,
            "scope_type": "agency",
            "scope_id": state["agency"]["id"],
            "connection_key": connection_key,
            "credentials": credentials,
        },
        headers=_auth(state["admin_token"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("actor_role", ["owner", "manager"])
def test_owner_and_manager_can_start_connect_and_discover_bound_accounts(actor_role: str) -> None:
    state = _bootstrap(actor_role=actor_role)
    _assign_tenant(state)
    _seed_agency_credential(state)
    app.state.oauth_adapters = {"google": _GoogleStartAdapter()}
    configured = api.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "redirect_uri": "https://dashboard.test/auth/google/callback",
            "enabled": True,
        },
        headers=_auth(state["admin_token"]),
    )
    assert configured.status_code == 200, configured.text

    started = api.get(
        f"/auth/google/start?intent=connect&next=/sync-monitor&agency_id={state['agency']['id']}",
        headers=_auth(state["actor_token"]),
        follow_redirects=False,
    )
    assert started.status_code == 302, started.text
    assert parse_qs(urlparse(started.headers["location"]).query)["state"]

    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda _credentials=None: [
            {
                "external_account_id": f"meta-{actor_role}",
                "name": f"Meta {actor_role}",
                "currency": "USD",
            }
        ]
    }
    discovered = api.post(
        "/ad-accounts/discover",
        json={
            "provider": "meta",
            "client_id": state["tenant"]["id"],
            "agency_id": state["agency"]["id"],
        },
        headers=_auth(state["actor_token"]),
    )
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["created"] == 1
    assert discovered.json()["items"][0]["client_id"] == state["tenant"]["id"]


def test_member_cannot_connect_discover_or_archive_but_can_sync_existing_account() -> None:
    state = _bootstrap(actor_role="member")
    _assign_tenant(state)
    credential = _seed_agency_credential(state)
    app.state.oauth_adapters = {"google": _GoogleStartAdapter()}
    configured = api.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "redirect_uri": "https://dashboard.test/auth/google/callback",
            "enabled": True,
        },
        headers=_auth(state["admin_token"]),
    )
    assert configured.status_code == 200, configured.text

    denied_connect = api.get(
        f"/auth/google/start?intent=connect&next=/sync-monitor&agency_id={state['agency']['id']}",
        headers=_auth(state["actor_token"]),
        follow_redirects=False,
    )
    assert denied_connect.status_code == 403
    assert _error_code(denied_connect) == "agency_manage_forbidden"

    denied_discovery = api.post(
        "/ad-accounts/discover",
        json={
            "provider": "meta",
            "client_id": state["tenant"]["id"],
            "agency_id": state["agency"]["id"],
        },
        headers=_auth(state["actor_token"]),
    )
    assert denied_discovery.status_code == 403
    assert _error_code(denied_discovery) == "agency_manage_forbidden"

    denied_archive = api.delete(
        f"/me/integration-connections/{credential['id']}",
        headers=_auth(state["actor_token"]),
    )
    assert denied_archive.status_code == 403
    assert _error_code(denied_archive) == "agency_manage_forbidden"

    account_response = api.post(
        "/ad-accounts",
        json={
            "client_id": state["tenant"]["id"],
            "platform": "meta",
            "external_account_id": "member-sync-existing",
            "name": "Existing account",
            "currency": "USD",
            "status": "active",
        },
        headers=_auth(state["admin_token"]),
    )
    assert account_response.status_code == 200, account_response.text
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda _external, date_from, _date_to, _credentials=None: [{"date": date_from}]
    }
    synced = api.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [account_response.json()["id"]], "force": True},
        headers=_auth(state["actor_token"]),
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["processed"] == 1
    assert synced.json()["success"] == 1


def test_agency_client_assignment_and_revocation_are_platform_admin_only() -> None:
    state = _bootstrap(actor_role="owner")

    denied_assign = api.post(
        f"/platform/agencies/{state['agency']['id']}/clients",
        json={"client_id": state["tenant"]["id"]},
        headers=_auth(state["actor_token"]),
    )
    assert denied_assign.status_code == 403
    assert _error_code(denied_assign) == "forbidden"

    binding = _assign_tenant(state)
    assert api.get(
        f"/clients/{state['tenant']['id']}", headers=_auth(state["actor_token"])
    ).status_code == 200

    denied_revoke = api.delete(
        f"/platform/agencies/{state['agency']['id']}/clients/{binding['id']}",
        headers=_auth(state["actor_token"]),
    )
    assert denied_revoke.status_code == 403
    assert _error_code(denied_revoke) == "forbidden"

    revoked = api.delete(
        f"/platform/agencies/{state['agency']['id']}/clients/{binding['id']}",
        headers=_auth(state["admin_token"]),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert api.get(
        f"/clients/{state['tenant']['id']}", headers=_auth(state["actor_token"])
    ).status_code == 403


def test_membership_activation_materializes_and_deactivation_removes_existing_client_scope() -> None:
    state = _bootstrap(actor_role="owner")
    _assign_tenant(state)
    late_member = _create_user("late-member@test.local", "agency", admin_token=state["admin_token"])
    late_token = _issue_token(late_member["id"], admin_token=state["admin_token"])

    assert api.get(
        f"/clients/{state['tenant']['id']}", headers=_auth(late_token)
    ).status_code == 403
    added = api.post(
        f"/platform/agencies/{state['agency']['id']}/members",
        json={"user_id": late_member["id"], "role": "member", "status": "active"},
        headers=_auth(state["admin_token"]),
    )
    assert added.status_code == 200, added.text
    assert api.get(
        f"/clients/{state['tenant']['id']}", headers=_auth(late_token)
    ).status_code == 200

    deactivated = api.post(
        f"/platform/agencies/{state['agency']['id']}/members/{added.json()['id']}/deactivate",
        headers=_auth(state["admin_token"]),
    )
    assert deactivated.status_code == 200, deactivated.text
    assert api.get(
        f"/clients/{state['tenant']['id']}", headers=_auth(late_token)
    ).status_code == 403

    reactivated = api.post(
        f"/platform/agencies/{state['agency']['id']}/members",
        json={"user_id": late_member["id"], "role": "member", "status": "active"},
        headers=_auth(state["admin_token"]),
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["id"] == added.json()["id"]
    assert api.get(
        f"/clients/{state['tenant']['id']}", headers=_auth(late_token)
    ).status_code == 200


def test_last_active_owner_cannot_be_deactivated_or_removed_until_successor_exists() -> None:
    state = _bootstrap(actor_role="owner")
    member_id = state["actor_member"]["id"]

    denied_deactivate = api.post(
        f"/platform/agencies/{state['agency']['id']}/members/{member_id}/deactivate",
        headers=_auth(state["admin_token"]),
    )
    assert denied_deactivate.status_code == 409
    assert _error_code(denied_deactivate) == "last_active_agency_owner"

    denied_remove = api.delete(
        f"/platform/agencies/{state['agency']['id']}/members/{member_id}",
        headers=_auth(state["admin_token"]),
    )
    assert denied_remove.status_code == 409
    assert _error_code(denied_remove) == "last_active_agency_owner"

    successor = _create_user("successor-owner@test.local", "agency", admin_token=state["admin_token"])
    promoted = api.post(
        f"/platform/agencies/{state['agency']['id']}/members",
        json={"user_id": successor["id"], "role": "owner", "status": "active"},
        headers=_auth(state["admin_token"]),
    )
    assert promoted.status_code == 200, promoted.text

    removed = api.delete(
        f"/platform/agencies/{state['agency']['id']}/members/{member_id}",
        headers=_auth(state["admin_token"]),
    )
    assert removed.status_code == 200
    remaining = api.get(
        f"/platform/agencies/{state['agency']['id']}/members",
        headers=_auth(state["admin_token"]),
    )
    assert remaining.status_code == 200
    active_owners = [row for row in remaining.json() if row["role"] == "owner" and row["status"] == "active"]
    assert [row["user_id"] for row in active_owners] == [successor["id"]]
