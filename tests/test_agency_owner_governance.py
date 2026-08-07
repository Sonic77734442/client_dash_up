from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AgencyCreate, AgencyMemberCreate, UserCreate, UserPatch
from app.services.auth_arch import InMemoryAuthStore, SqliteAuthStore
from app.services.platform_admin import InMemoryPlatformAdminStore, SqlitePlatformAdminStore


client = TestClient(app)


def _stores(kind: str, tmp_path):
    if kind == "sqlite":
        db_path = str(tmp_path / "agency-owner-governance.db")
        auth_store = SqliteAuthStore(db_path)
        return auth_store, SqlitePlatformAdminStore(db_path, auth_store)
    auth_store = InMemoryAuthStore()
    return auth_store, InMemoryPlatformAdminStore(auth_store)


def _create_user(auth_store, *, kind: str, label: str, role: str = "agency"):
    return auth_store.create_user(
        UserCreate(
            email=f"{label}-{kind}@owner-governance.test",
            name=label,
            role=role,
            status="active",
        )
    )


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_manager_cannot_mutate_owner_at_store_boundary(kind, tmp_path):
    auth_store, store = _stores(kind, tmp_path)
    owner = _create_user(auth_store, kind=kind, label="owner")
    manager = _create_user(auth_store, kind=kind, label="manager")
    second_owner = _create_user(auth_store, kind=kind, label="second-owner")
    agency = store.create_agency(AgencyCreate(name=f"Manager guard {kind}", status="active"))
    owner_member = store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=owner.id, role="owner", status="active"),
    )
    store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=second_owner.id, role="owner", status="active"),
    )
    store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=manager.id, role="manager", status="active"),
    )

    with pytest.raises(HTTPException) as deactivate_exc:
        store.deactivate_member(agency.id, owner_member.id, actor_user_id=manager.id)
    assert deactivate_exc.value.status_code == 403
    assert deactivate_exc.value.detail["code"] == "agency_owner_protected"

    with pytest.raises(HTTPException) as remove_exc:
        store.remove_member(agency.id, owner_member.id, actor_user_id=manager.id)
    assert remove_exc.value.status_code == 403
    assert remove_exc.value.detail["code"] == "agency_owner_protected"
    assert next(row for row in store.list_members(agency.id) if row.id == owner_member.id).status == "active"


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_last_owner_is_protected_and_self_removal_requires_transfer(kind, tmp_path):
    auth_store, store = _stores(kind, tmp_path)
    admin = _create_user(auth_store, kind=kind, label="admin", role="admin")
    first_owner = _create_user(auth_store, kind=kind, label="first-owner")
    successor = _create_user(auth_store, kind=kind, label="successor")
    agency = store.create_agency(AgencyCreate(name=f"Owner transfer {kind}", status="active"))
    first_member = store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=first_owner.id, role="owner", status="active"),
    )

    with pytest.raises(HTTPException) as self_remove_exc:
        store.remove_member(agency.id, first_member.id, actor_user_id=first_owner.id)
    assert self_remove_exc.value.status_code == 409
    assert self_remove_exc.value.detail["code"] == "last_active_agency_owner"

    with pytest.raises(HTTPException) as admin_deactivate_exc:
        store.deactivate_member(
            agency.id,
            first_member.id,
            actor_user_id=admin.id,
            actor_is_platform_admin=True,
        )
    assert admin_deactivate_exc.value.status_code == 409

    successor_member = store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=successor.id, role="manager", status="active"),
    )
    promoted = store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=successor.id, role="owner", status="active"),
        actor_user_id=admin.id,
        actor_is_platform_admin=True,
    )
    assert promoted.id == successor_member.id
    assert promoted.role == "owner"

    store.remove_member(agency.id, first_member.id, actor_user_id=first_owner.id)
    remaining = store.list_members(agency.id)
    assert [row.user_id for row in remaining if row.role == "owner" and row.status == "active"] == [successor.id]


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_concurrent_owner_deactivation_cannot_remove_both_owners(kind, tmp_path):
    auth_store, store = _stores(kind, tmp_path)
    admin = _create_user(auth_store, kind=kind, label="race-admin", role="admin")
    first_owner = _create_user(auth_store, kind=kind, label="race-first")
    second_owner = _create_user(auth_store, kind=kind, label="race-second")
    agency = store.create_agency(AgencyCreate(name=f"Concurrent owners {kind}", status="active"))
    first_member = store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=first_owner.id, role="owner", status="active"),
    )
    second_member = store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=second_owner.id, role="owner", status="active"),
    )
    barrier = Barrier(2)

    def deactivate(member_id):
        barrier.wait(timeout=5)
        try:
            store.deactivate_member(
                agency.id,
                member_id,
                actor_user_id=admin.id,
                actor_is_platform_admin=True,
            )
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(deactivate, [first_member.id, second_member.id]))

    assert sorted(results) == [200, 409]
    active_owners = [
        row for row in store.list_members(agency.id) if row.role == "owner" and row.status == "active"
    ]
    assert len(active_owners) == 1


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_user_patch_and_delete_cannot_bypass_last_owner_guard(kind, tmp_path):
    auth_store, store = _stores(kind, tmp_path)
    owner = _create_user(auth_store, kind=kind, label="user-mutation-owner")
    agency = store.create_agency(AgencyCreate(name=f"User mutation guard {kind}", status="active"))
    store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=owner.id, role="owner", status="active"),
    )

    for payload in (UserPatch(status="inactive"), UserPatch(role="client")):
        with pytest.raises(HTTPException) as patch_exc:
            auth_store.patch_user(owner.id, payload)
        assert patch_exc.value.status_code == 409
        assert patch_exc.value.detail["code"] == "last_active_agency_owner"

    with pytest.raises(HTTPException) as delete_exc:
        auth_store.delete_user(owner.id)
    assert delete_exc.value.status_code == 409
    assert delete_exc.value.detail["code"] == "last_active_agency_owner"

    successor = _create_user(auth_store, kind=kind, label="user-mutation-successor")
    store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=successor.id, role="owner", status="active"),
    )
    assert auth_store.patch_user(owner.id, UserPatch(status="inactive")).status == "inactive"


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_concurrent_user_deactivation_cannot_remove_both_agency_owners(kind, tmp_path):
    auth_store, store = _stores(kind, tmp_path)
    first_owner = _create_user(auth_store, kind=kind, label="user-race-first")
    second_owner = _create_user(auth_store, kind=kind, label="user-race-second")
    agency = store.create_agency(AgencyCreate(name=f"User mutation race {kind}", status="active"))
    for owner in (first_owner, second_owner):
        store.upsert_member(
            agency.id,
            AgencyMemberCreate(user_id=owner.id, role="owner", status="active"),
        )
    barrier = Barrier(2)

    def deactivate(user_id):
        barrier.wait(timeout=5)
        try:
            auth_store.patch_user(user_id, UserPatch(status="inactive"))
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(deactivate, [first_owner.id, second_owner.id]))

    assert sorted(results) == [200, 409]
    active_owner_ids = {
        member.user_id
        for member in store.list_members(agency.id)
        if member.role == "owner"
        and member.status == "active"
        and auth_store.get_user(member.user_id).status == "active"
    }
    assert len(active_owner_ids) == 1


def test_manager_is_blocked_from_owner_mutation_through_endpoint():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.cookies.clear()

    def create_user(email: str, role: str):
        response = client.post(
            "/auth/internal/users",
            json={"email": email, "name": role, "role": role, "status": "active"},
        )
        assert response.status_code == 200
        return response.json()

    def token_for(user_id: str):
        response = client.post("/auth/internal/sessions/issue", json={"user_id": user_id, "ttl_minutes": 60})
        assert response.status_code == 200
        return response.json()["token"]

    admin = create_user("api-admin@owner-governance.test", "admin")
    owner = create_user("api-owner@owner-governance.test", "agency")
    manager = create_user("api-manager@owner-governance.test", "agency")
    admin_header = {"Authorization": f"Bearer {token_for(admin['id'])}"}
    manager_header = {"Authorization": f"Bearer {token_for(manager['id'])}"}
    agency_response = client.post(
        "/platform/agencies",
        json={"name": "API owner governance", "status": "active", "plan": "starter"},
        headers=admin_header,
    )
    assert agency_response.status_code == 200
    agency_id = agency_response.json()["id"]
    owner_member = client.post(
        f"/platform/agencies/{agency_id}/members",
        json={"user_id": owner["id"], "role": "owner", "status": "active"},
        headers=admin_header,
    ).json()
    manager_response = client.post(
        f"/platform/agencies/{agency_id}/members",
        json={"user_id": manager["id"], "role": "manager", "status": "active"},
        headers=admin_header,
    )
    assert manager_response.status_code == 200

    admin_denied = client.delete(
        f"/platform/agencies/{agency_id}/members/{owner_member['id']}",
        headers=admin_header,
    )
    assert admin_denied.status_code == 409
    assert admin_denied.json()["error"]["code"] == "last_active_agency_owner"

    denied = client.post(
        f"/platform/agencies/{agency_id}/members/{owner_member['id']}/deactivate",
        headers=manager_header,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "agency_owner_protected"
