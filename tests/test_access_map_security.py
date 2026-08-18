from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db import init_sqlite, sqlite_conn
from app.main import app
from app.schemas import (
    AgencyClientAccessCreate,
    AgencyCreate,
    AgencyMemberCreate,
    ClientCreate,
    UserClientAccessCreate,
    UserCreate,
)
from app.services.auth_arch import SqliteAuthStore
from app.services.clients import SqliteClientStore
from app.services.platform_admin import SqlitePlatformAdminStore


api = TestClient(app)


def _reset() -> None:
    assert api.post("/_testing/use-inmemory-stores").status_code == 200


def _user(label: str, role: str, status: str = "active") -> dict:
    response = api.post(
        "/auth/internal/users",
        json={
            "email": f"{label}@access-map.test",
            "name": label,
            "role": role,
            "status": status,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _token(user_id: str) -> str:
    response = api.post(
        "/auth/internal/sessions/issue",
        json={"user_id": user_id, "ttl_minutes": 60},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(label: str, admin_token: str) -> dict:
    response = api.post(
        "/clients",
        json={"name": label, "status": "active", "default_currency": "USD"},
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def test_direct_assignment_is_client_only_and_marker_must_match_platform_role() -> None:
    _reset()
    admin = _user("marker-admin", "admin")
    admin_token = _token(admin["id"])
    tenant = _client("Marker tenant", admin_token)
    viewer = _user("marker-viewer", "client")
    agency_user = _user("marker-agency", "agency")

    viewer_mismatch = api.post(
        "/auth/internal/access",
        json={"user_id": viewer["id"], "client_id": tenant["id"], "role": "agency"},
        headers=_auth(admin_token),
    )
    assert viewer_mismatch.status_code == 409
    assert _error_code(viewer_mismatch) == "access_role_mismatch"

    agency_mismatch = api.post(
        "/auth/internal/access",
        json={"user_id": agency_user["id"], "client_id": tenant["id"], "role": "client"},
        headers=_auth(admin_token),
    )
    assert agency_mismatch.status_code == 409
    assert _error_code(agency_mismatch) == "agency_access_managed_by_membership"

    assert api.post(
        "/auth/internal/access",
        json={"user_id": viewer["id"], "client_id": tenant["id"], "role": "client"},
        headers=_auth(admin_token),
    ).status_code == 200
    agency_direct = api.post(
        "/auth/internal/access",
        json={"user_id": agency_user["id"], "client_id": tenant["id"], "role": "agency"},
        headers=_auth(admin_token),
    )
    assert agency_direct.status_code == 409
    assert _error_code(agency_direct) == "agency_access_managed_by_membership"

    admin_grant = api.post(
        "/auth/internal/access",
        json={"user_id": admin["id"], "client_id": tenant["id"], "role": "client"},
        headers=_auth(admin_token),
    )
    assert admin_grant.status_code == 409
    assert _error_code(admin_grant) == "admin_access_not_required"


def test_admin_can_clean_up_orphan_agency_grant_but_not_backed_grant() -> None:
    _reset()
    admin = _user("orphan-admin", "admin")
    admin_token = _token(admin["id"])
    agency_user = _user("orphan-agency", "agency")
    agency_token = _token(agency_user["id"])
    tenant = _client("Orphan tenant", admin_token)

    # Internal materialization is still available to the agency store. With no
    # membership/binding this simulates a legacy orphan from the old UI.
    app.state.auth_store.assign_client_access(
        UserClientAccessCreate(
            user_id=UUID(agency_user["id"]),
            client_id=UUID(tenant["id"]),
            role="agency",
        )
    )
    assert api.get(f"/clients/{tenant['id']}", headers=_auth(agency_token)).status_code == 200

    revoked = api.delete(
        f"/auth/internal/access/{agency_user['id']}/{tenant['id']}",
        headers=_auth(admin_token),
    )
    assert revoked.status_code == 200, revoked.text
    assert api.get(f"/clients/{tenant['id']}", headers=_auth(agency_token)).status_code == 403


def test_client_to_agency_role_transition_drops_direct_scope_until_membership_materializes() -> None:
    _reset()
    admin = _user("transition-admin", "admin")
    admin_token = _token(admin["id"])
    old_tenant = _client("Old direct tenant", admin_token)
    agency_tenant = _client("Canonical agency tenant", admin_token)
    user = _user("transition-user", "client")
    user_token = _token(user["id"])

    assert api.post(
        "/auth/internal/access",
        json={"user_id": user["id"], "client_id": old_tenant["id"], "role": "client"},
        headers=_auth(admin_token),
    ).status_code == 200
    assert api.get(f"/clients/{old_tenant['id']}", headers=_auth(user_token)).status_code == 200

    promoted = api.patch(
        f"/auth/internal/users/{user['id']}",
        json={"role": "agency"},
        headers=_auth(admin_token),
    )
    assert promoted.status_code == 200, promoted.text
    assert api.get(f"/clients/{old_tenant['id']}", headers=_auth(user_token)).status_code == 403

    agency = api.post(
        "/platform/agencies",
        json={"name": "Transition agency", "status": "active", "plan": "starter"},
        headers=_auth(admin_token),
    ).json()
    assert api.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": user["id"], "role": "member", "status": "active"},
        headers=_auth(admin_token),
    ).status_code == 200
    assert api.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": agency_tenant["id"]},
        headers=_auth(admin_token),
    ).status_code == 200
    assert api.get(f"/clients/{agency_tenant['id']}", headers=_auth(user_token)).status_code == 200
    assert api.get(f"/clients/{old_tenant['id']}", headers=_auth(user_token)).status_code == 403


def test_assignment_rejects_inactive_user_and_archived_client() -> None:
    _reset()
    admin = _user("state-admin", "admin")
    admin_token = _token(admin["id"])
    inactive_viewer = _user("inactive-viewer", "client", status="inactive")
    active_viewer = _user("active-viewer", "client")
    tenant = _client("Archived assignment tenant", admin_token)

    inactive = api.post(
        "/auth/internal/access",
        json={"user_id": inactive_viewer["id"], "client_id": tenant["id"], "role": "client"},
        headers=_auth(admin_token),
    )
    assert inactive.status_code == 409
    assert _error_code(inactive) == "access_user_inactive"

    archived = api.patch(
        f"/clients/{tenant['id']}",
        json={"status": "archived"},
        headers=_auth(admin_token),
    )
    assert archived.status_code == 200, archived.text
    inactive_tenant = api.post(
        "/auth/internal/access",
        json={"user_id": active_viewer["id"], "client_id": tenant["id"], "role": "client"},
        headers=_auth(admin_token),
    )
    assert inactive_tenant.status_code == 409
    assert _error_code(inactive_tenant) == "access_client_inactive"


def test_admin_can_revoke_direct_client_access_and_reassign_solo_owner() -> None:
    _reset()
    admin = _user("revoke-admin", "admin")
    admin_token = _token(admin["id"])
    first_tenant = _client("First direct tenant", admin_token)
    second_tenant = _client("Second direct tenant", admin_token)
    viewer = _user("revoke-viewer", "client")
    solo = _user("reassign-solo", "solo_client")
    viewer_token = _token(viewer["id"])

    for user in (viewer, solo):
        assigned = api.post(
            "/auth/internal/access",
            json={"user_id": user["id"], "client_id": first_tenant["id"], "role": "client"},
            headers=_auth(admin_token),
        )
        assert assigned.status_code == 200, assigned.text

    assert api.get(f"/clients/{first_tenant['id']}", headers=_auth(viewer_token)).status_code == 200
    revoked_viewer = api.delete(
        f"/auth/internal/access/{viewer['id']}/{first_tenant['id']}",
        headers=_auth(admin_token),
    )
    assert revoked_viewer.status_code == 200
    assert revoked_viewer.json()["status"] == "revoked"
    assert api.get(f"/clients/{first_tenant['id']}", headers=_auth(viewer_token)).status_code == 403

    revoked_solo = api.delete(
        f"/auth/internal/access/{solo['id']}/{first_tenant['id']}",
        headers=_auth(admin_token),
    )
    assert revoked_solo.status_code == 200
    reassigned = api.post(
        "/auth/internal/access",
        json={"user_id": solo["id"], "client_id": second_tenant["id"], "role": "client"},
        headers=_auth(admin_token),
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["client_id"] == second_tenant["id"]


def test_agency_access_is_revoked_only_through_agency_and_legacy_marker_cannot_survive() -> None:
    _reset()
    admin = _user("agency-revoke-admin", "admin")
    admin_token = _token(admin["id"])
    agency_user = _user("agency-revoke-user", "agency")
    agency_token = _token(agency_user["id"])
    tenant = _client("Agency managed tenant", admin_token)
    agency = api.post(
        "/platform/agencies",
        json={"name": "Access map agency", "status": "active", "plan": "starter"},
        headers=_auth(admin_token),
    ).json()
    member = api.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "owner", "status": "active"},
        headers=_auth(admin_token),
    )
    assert member.status_code == 200, member.text
    binding_response = api.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=_auth(admin_token),
    )
    assert binding_response.status_code == 200, binding_response.text
    binding = binding_response.json()

    direct_revoke = api.delete(
        f"/auth/internal/access/{agency_user['id']}/{tenant['id']}",
        headers=_auth(admin_token),
    )
    assert direct_revoke.status_code == 409
    assert _error_code(direct_revoke) == "agency_access_managed_by_membership"
    assert api.get(f"/clients/{tenant['id']}", headers=_auth(agency_token)).status_code == 200

    # Simulate the dangerous legacy state created by the old UI. Rebuilding
    # agency access must delete it regardless of its stale marker.
    access_store = app.state.auth_store.access
    key = f"{agency_user['id']}:{tenant['id']}"
    access_store[key] = access_store[key].model_copy(update={"role": "client"})
    revoked_binding = api.delete(
        f"/platform/agencies/{agency['id']}/clients/{binding['id']}",
        headers=_auth(admin_token),
    )
    assert revoked_binding.status_code == 200, revoked_binding.text
    assert api.get(f"/clients/{tenant['id']}", headers=_auth(agency_token)).status_code == 403
    audit_rows = api.get(
        "/audit/logs?event_type=agency.client_access.revoked",
        headers=_auth(admin_token),
    )
    assert audit_rows.status_code == 200
    assert any(row["resource_id"] == binding["id"] for row in audit_rows.json())


def test_sqlite_startup_reconciliation_removes_orphans_and_normalizes_markers(tmp_path) -> None:
    db_path = str(tmp_path / "access-reconciliation.db")
    auth_store = SqliteAuthStore(db_path)
    client_store = SqliteClientStore(db_path)
    platform_store = SqlitePlatformAdminStore(db_path, auth_store)

    backed_user = auth_store.create_user(
        UserCreate(email="backed@migration.test", name="Backed", role="agency", status="active")
    )
    orphan_user = auth_store.create_user(
        UserCreate(email="orphan@migration.test", name="Orphan", role="agency", status="active")
    )
    viewer = auth_store.create_user(
        UserCreate(email="viewer@migration.test", name="Viewer", role="client", status="active")
    )
    admin = auth_store.create_user(
        UserCreate(email="admin@migration.test", name="Admin", role="admin", status="active")
    )
    backed_client = client_store.create(ClientCreate(name="Backed client", default_currency="USD"))
    orphan_client = client_store.create(ClientCreate(name="Orphan client", default_currency="USD"))
    agency = platform_store.create_agency(AgencyCreate(name="Migration agency", status="active"))
    platform_store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=backed_user.id, role="member", status="active"),
    )
    platform_store.assign_client(
        agency.id,
        AgencyClientAccessCreate(client_id=backed_client.id),
    )

    auth_store.assign_client_access(
        UserClientAccessCreate(user_id=orphan_user.id, client_id=orphan_client.id, role="agency")
    )
    auth_store.assign_client_access(
        UserClientAccessCreate(user_id=viewer.id, client_id=orphan_client.id, role="client")
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with sqlite_conn(db_path) as conn:
        conn.execute(
            "UPDATE user_client_access SET role='client' WHERE user_id=? AND client_id=?",
            (str(backed_user.id), str(backed_client.id)),
        )
        conn.execute(
            "UPDATE user_client_access SET role='agency' WHERE user_id=? AND client_id=?",
            (str(viewer.id), str(orphan_client.id)),
        )
        conn.execute(
            """
            INSERT INTO user_client_access (id, user_id, client_id, role, created_at, updated_at)
            VALUES (?, ?, ?, 'client', ?, ?)
            """,
            (str(uuid4()), str(admin.id), str(orphan_client.id), now, now),
        )
        conn.commit()

    init_sqlite(db_path)

    assert [(row.client_id, row.role) for row in auth_store.list_client_access(backed_user.id)] == [
        (backed_client.id, "agency")
    ]
    assert auth_store.list_client_access(orphan_user.id) == []
    assert [(row.client_id, row.role) for row in auth_store.list_client_access(viewer.id)] == [
        (orphan_client.id, "client")
    ]
    assert auth_store.list_client_access(admin.id) == []
