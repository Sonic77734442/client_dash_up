from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException

from app.schemas import (
    AgencyClientAccessCreate,
    AgencyCreate,
    AgencyMemberCreate,
    AgencyPatch,
    ClientCreate,
    UserCreate,
    UserPatch,
)
from app.services.auth_arch import InMemoryAuthStore, SqliteAuthStore
from app.services.clients import InMemoryClientStore, SqliteClientStore
from app.services.platform_admin import InMemoryPlatformAdminStore, SqlitePlatformAdminStore


def _stores(kind: str, tmp_path):
    if kind == "sqlite":
        db_path = str(tmp_path / f"acl-{kind}.db")
        auth_store = SqliteAuthStore(db_path)
        return auth_store, SqliteClientStore(db_path), SqlitePlatformAdminStore(db_path, auth_store)
    auth_store = InMemoryAuthStore()
    return auth_store, InMemoryClientStore(), InMemoryPlatformAdminStore(auth_store)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_agency_status_rebuilds_materialized_access_for_both_stores(kind, tmp_path):
    auth_store, client_store, platform_store = _stores(kind, tmp_path)
    first_user = auth_store.create_user(
        UserCreate(email=f"first-{kind}@test.local", name="First", role="agency", status="active")
    )
    second_user = auth_store.create_user(
        UserCreate(email=f"second-{kind}@test.local", name="Second", role="agency", status="active")
    )
    first_client = client_store.create(ClientCreate(name="First tenant", default_currency="USD"))
    second_client = client_store.create(ClientCreate(name="Second tenant", default_currency="USD"))
    agency = platform_store.create_agency(AgencyCreate(name=f"Agency {kind}", status="active"))
    platform_store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=first_user.id, role="owner", status="active"),
    )
    platform_store.assign_client(agency.id, AgencyClientAccessCreate(client_id=first_client.id))
    assert {row.client_id for row in auth_store.list_client_access(first_user.id)} == {first_client.id}

    platform_store.patch_agency(agency.id, AgencyPatch(status="suspended"))
    assert auth_store.list_client_access(first_user.id) == []

    # These mutations are valid configuration changes, but they must not grant
    # live tenant scope until the agency is active again.
    platform_store.assign_client(agency.id, AgencyClientAccessCreate(client_id=second_client.id))
    platform_store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=second_user.id, role="member", status="active"),
    )
    assert auth_store.list_client_access(first_user.id) == []
    assert auth_store.list_client_access(second_user.id) == []

    platform_store.patch_agency(agency.id, AgencyPatch(status="active"))
    expected = {first_client.id, second_client.id}
    assert {row.client_id for row in auth_store.list_client_access(first_user.id)} == expected
    assert {row.client_id for row in auth_store.list_client_access(second_user.id)} == expected


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
@pytest.mark.parametrize("target_role", ["client", "admin"])
def test_role_transition_removes_agency_grants_without_later_regrant(kind, target_role, tmp_path):
    auth_store, client_store, platform_store = _stores(kind, tmp_path)
    user = auth_store.create_user(
        UserCreate(email=f"demote-{kind}-{target_role}@test.local", name="Member", role="agency", status="active")
    )
    tenant = client_store.create(ClientCreate(name="Tenant", default_currency="USD"))
    agency = platform_store.create_agency(AgencyCreate(name=f"Demotion {kind} {target_role}", status="active"))
    platform_store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=user.id, role="member", status="active"),
    )
    platform_store.assign_client(agency.id, AgencyClientAccessCreate(client_id=tenant.id))
    assert len(auth_store.list_client_access(user.id)) == 1

    auth_store.patch_user(user.id, UserPatch(role=target_role))
    assert auth_store.list_client_access(user.id) == []
    platform_store.patch_agency(agency.id, AgencyPatch(status="suspended"))
    platform_store.patch_agency(agency.id, AgencyPatch(status="active"))
    assert auth_store.list_client_access(user.id) == []


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_last_active_admin_patch_guard_has_store_parity(kind, tmp_path):
    auth_store, _, _ = _stores(kind, tmp_path)
    first = auth_store.create_user(
        UserCreate(email=f"first-admin-{kind}@test.local", name="First admin", role="admin", status="active")
    )

    with pytest.raises(HTTPException) as demote_exc:
        auth_store.patch_user(first.id, UserPatch(role="agency"))
    assert demote_exc.value.status_code == 409
    with pytest.raises(HTTPException) as inactive_exc:
        auth_store.patch_user(first.id, UserPatch(status="inactive"))
    assert inactive_exc.value.status_code == 409
    assert auth_store.get_user(first.id).role == "admin"
    assert auth_store.get_user(first.id).status == "active"

    auth_store.create_user(
        UserCreate(email=f"second-admin-{kind}@test.local", name="Second admin", role="admin", status="active")
    )
    updated = auth_store.patch_user(first.id, UserPatch(status="inactive"))
    assert updated.status == "inactive"


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_concurrent_admin_delete_cannot_remove_all_active_admins(kind, tmp_path):
    auth_store, _, _ = _stores(kind, tmp_path)
    admins = [
        auth_store.create_user(
            UserCreate(
                email=f"delete-race-{index}-{kind}@test.local",
                name=f"Admin {index}",
                role="admin",
                status="active",
            )
        )
        for index in range(2)
    ]
    barrier = Barrier(2)

    def delete(user_id):
        barrier.wait(timeout=5)
        try:
            auth_store.delete_user(user_id)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(delete, [admin.id for admin in admins]))

    assert sorted(results) == [200, 409]
    active_admins = [
        user for user in auth_store.list_users() if user.role == "admin" and user.status == "active"
    ]
    assert len(active_admins) == 1
