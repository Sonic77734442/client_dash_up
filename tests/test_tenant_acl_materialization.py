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
    UserClientAccessCreate,
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
def test_reactivating_agency_user_restores_scope_from_active_memberships(kind, tmp_path):
    auth_store, client_store, platform_store = _stores(kind, tmp_path)
    user = auth_store.create_user(
        UserCreate(email=f"reactivate-{kind}@test.local", name="Member", role="agency", status="active")
    )
    tenant = client_store.create(ClientCreate(name="Reactivation tenant", default_currency="USD"))
    agency = platform_store.create_agency(AgencyCreate(name=f"Reactivation {kind}", status="active"))
    platform_store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=user.id, role="member", status="active"),
    )
    platform_store.assign_client(agency.id, AgencyClientAccessCreate(client_id=tenant.id))
    assert {row.client_id for row in auth_store.list_client_access(user.id)} == {tenant.id}

    auth_store.patch_user(user.id, UserPatch(status="inactive"))
    assert auth_store.list_client_access(user.id) == []
    auth_store.patch_user(user.id, UserPatch(status="active"))
    assert {row.client_id for row in auth_store.list_client_access(user.id)} == {tenant.id}


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
def test_client_direct_grant_survives_cleanup_of_stale_agency_membership(kind, tmp_path):
    auth_store, client_store, platform_store = _stores(kind, tmp_path)
    user = auth_store.create_user(
        UserCreate(email=f"stale-member-{kind}@test.local", name="Member", role="agency", status="active")
    )
    agency_client = client_store.create(ClientCreate(name="Agency tenant", default_currency="USD"))
    direct_client = client_store.create(ClientCreate(name="Direct tenant", default_currency="USD"))
    agency = platform_store.create_agency(AgencyCreate(name=f"Stale membership {kind}", status="active"))
    platform_store.upsert_member(
        agency.id,
        AgencyMemberCreate(user_id=user.id, role="member", status="active"),
    )
    platform_store.assign_client(agency.id, AgencyClientAccessCreate(client_id=agency_client.id))

    auth_store.patch_user(user.id, UserPatch(role="client"))
    assert auth_store.list_client_access(user.id) == []
    auth_store.assign_client_access(
        UserClientAccessCreate(user_id=user.id, client_id=direct_client.id, role="client")
    )

    # The still-present membership is stale, but its lifecycle must not erase
    # an independent direct viewer grant after the global-role demotion.
    platform_store.patch_agency(agency.id, AgencyPatch(status="suspended"))
    assert {row.client_id for row in auth_store.list_client_access(user.id)} == {direct_client.id}


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_multi_agency_rebuild_keeps_scope_from_the_other_active_agency(kind, tmp_path):
    auth_store, client_store, platform_store = _stores(kind, tmp_path)
    user = auth_store.create_user(
        UserCreate(email=f"multi-agency-{kind}@test.local", name="Member", role="agency", status="active")
    )
    first_client = client_store.create(ClientCreate(name="First agency tenant", default_currency="USD"))
    second_client = client_store.create(ClientCreate(name="Second agency tenant", default_currency="USD"))
    first_agency = platform_store.create_agency(AgencyCreate(name=f"First multi {kind}", status="active"))
    second_agency = platform_store.create_agency(AgencyCreate(name=f"Second multi {kind}", status="active"))
    for agency in (first_agency, second_agency):
        platform_store.upsert_member(
            agency.id,
            AgencyMemberCreate(user_id=user.id, role="member", status="active"),
        )
    first_binding = platform_store.assign_client(
        first_agency.id,
        AgencyClientAccessCreate(client_id=first_client.id),
    )
    platform_store.assign_client(
        second_agency.id,
        AgencyClientAccessCreate(client_id=second_client.id),
    )
    assert {row.client_id for row in auth_store.list_client_access(user.id)} == {
        first_client.id,
        second_client.id,
    }

    platform_store.revoke_client(first_agency.id, first_binding.id)
    assert {row.client_id for row in auth_store.list_client_access(user.id)} == {second_client.id}


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_solo_owner_must_revoke_archived_grant_before_reassignment(kind, tmp_path):
    auth_store, client_store, _platform_store = _stores(kind, tmp_path)
    user = auth_store.create_user(
        UserCreate(email=f"solo-archived-{kind}@test.local", name="Solo", role="solo_client", status="active")
    )
    archived_client = client_store.create(ClientCreate(name="Archived solo tenant", default_currency="USD"))
    new_client = client_store.create(ClientCreate(name="New solo tenant", default_currency="USD"))
    auth_store.assign_client_access(
        UserClientAccessCreate(user_id=user.id, client_id=archived_client.id, role="client")
    )
    client_store.archive(archived_client.id)

    with pytest.raises(HTTPException) as exc:
        auth_store.assign_client_access(
            UserClientAccessCreate(user_id=user.id, client_id=new_client.id, role="client")
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "solo_owner_provisioning_conflict"

    auth_store.remove_client_access(user.id, archived_client.id)
    reassigned = auth_store.assign_client_access(
        UserClientAccessCreate(user_id=user.id, client_id=new_client.id, role="client")
    )
    assert reassigned.client_id == new_client.id


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
