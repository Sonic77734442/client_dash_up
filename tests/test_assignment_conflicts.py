import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import init_sqlite, sqlite_conn
from app.main import app
from app.schemas import (
    AssignmentConflictResolveRequest,
    BudgetCreate,
    ClientCreate,
    UserCreate,
)
from app.services.ad_accounts import SqliteAdAccountStore, active_assignment_conflict_ids
from app.services.ad_stats import SqliteAdStatsStore
from app.services.assignment_conflicts import AssignmentConflictService
from app.services.audit_log import SqliteAuditLogStore
from app.services.auth_arch import SqliteAuthStore
from app.services.budgets import SqliteBudgetStore
from app.services.clients import SqliteClientStore
from app.services.platform_admin import SqlitePlatformAdminStore


api = TestClient(app)


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _reset_api_state():
    api.headers.pop("Authorization", None)
    api.cookies.clear()
    assert api.post("/_testing/use-inmemory-stores").status_code == 200
    admin = api.post(
        "/auth/internal/users",
        json={"email": f"admin-{uuid4()}@conflicts.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = api.post(
        "/auth/internal/sessions/issue",
        json={"user_id": admin.json()["id"], "ttl_minutes": 60},
    )
    assert issued.status_code == 200
    token = issued.json()["token"]
    api.headers.update(_auth_header(token))
    return admin.json(), token


def _api_user(role: str):
    result = api.post(
        "/auth/internal/users",
        json={
            "email": f"{role}-{uuid4()}@conflicts.local",
            "name": role.title(),
            "role": role,
            "status": "active",
        },
    )
    assert result.status_code == 200
    issued = api.post(
        "/auth/internal/sessions/issue",
        json={"user_id": result.json()["id"], "ttl_minutes": 60},
    )
    assert issued.status_code == 200
    return result.json(), issued.json()["token"]


def _api_client(name: str):
    result = api.post(
        "/clients",
        json={"name": name, "status": "active", "default_currency": "USD"},
    )
    assert result.status_code == 200
    return result.json()


def _api_account(client_id: str, platform: str, external_id: str):
    result = api.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external_id,
            "name": f"{platform}-{external_id}",
            "currency": "USD",
            "status": "active",
        },
    )
    assert result.status_code == 200
    return result.json()


def _force_legacy_identity(account_id: str, *, platform: str, external_id: str) -> None:
    store = app.state.ad_account_store
    key = UUID(account_id)
    store.items[key] = store.items[key].model_copy(
        update={
            "platform": platform,
            "external_account_id": external_id,
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
    )


def _seed_api_conflict(*, platform: str = "meta", first_external: str = "act_123", legacy_external: str = "123"):
    first_client = _api_client(f"First {platform}")
    second_client = _api_client(f"Second {platform}")
    first = _api_account(first_client["id"], platform, first_external)
    second = _api_account(second_client["id"], platform, f"temporary-{uuid4()}")
    _force_legacy_identity(second["id"], platform=platform, external_id=legacy_external)
    return first_client, second_client, first, second


def _resolve_body(group: dict, winner_account_id: str, *, policy: str = "reject", note: str = "Reviewed"):
    return {
        "winner_account_id": winner_account_id,
        "expected_account_ids": group["account_ids"],
        "group_version": group["group_version"],
        "loser_budget_policy": policy,
        "note": note,
    }


def test_conflict_list_uses_canonical_meta_and_google_identity_and_ignores_deleted_clients():
    _reset_api_state()
    meta_first_client, meta_second_client, _, _ = _seed_api_conflict(
        platform="meta",
        first_external="act_123",
        legacy_external="123",
    )
    _seed_api_conflict(
        platform="google",
        first_external="123-456-7890",
        legacy_external="1234567890",
    )

    first = api.get("/ad-accounts/assignment-conflicts")
    second = api.get("/ad-accounts/assignment-conflicts")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["count"] == 2
    assert body["summary"] == {"conflict_groups": 2, "conflicted_accounts": 4, "active_budgets": 0}
    groups = {(item["platform"], item["canonical_external_account_id"]): item for item in body["items"]}
    assert set(groups) == {("meta", "123"), ("google", "1234567890")}
    assert all(item["group_id"].startswith("acg_") for item in groups.values())
    assert all(item["group_version"].startswith("acv_") for item in groups.values())
    assert all(item["summary"]["candidate_count"] == 2 for item in groups.values())

    archived = api.delete(f"/clients/{meta_second_client['id']}")
    assert archived.status_code == 200
    after_archive = api.get("/ad-accounts/assignment-conflicts")
    assert after_archive.status_code == 200
    assert after_archive.json()["count"] == 1
    assert after_archive.json()["items"][0]["platform"] == "google"
    assert api.get(f"/clients/{meta_first_client['id']}").status_code == 200


def test_resolve_requires_explicit_budget_policy_preserves_stats_and_writes_audit():
    admin, _ = _reset_api_state()
    _, _, winner, loser = _seed_api_conflict()
    for account, spend in ((winner, "100.00"), (loser, "200.00")):
        ingested = api.post(
            "/ad-stats/ingest",
            json={
                "rows": [
                    {
                        "ad_account_id": account["id"],
                        "date": "2026-08-01",
                        "platform": "meta",
                        "impressions": 1000,
                        "clicks": 50,
                        "spend": spend,
                        "conversions": "5.00",
                    }
                ]
            },
        )
        assert ingested.status_code == 200
    budget = api.post(
        "/budgets",
        json={
            "client_id": loser["client_id"],
            "scope": "account",
            "account_id": loser["id"],
            "amount": "1000.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "note": "Must be handled explicitly",
        },
    )
    assert budget.status_code == 200

    listed = api.get("/ad-accounts/assignment-conflicts").json()
    group = listed["items"][0]
    assert group["summary"]["active_budget_count"] == 1
    assert all(candidate["latest_stat"]["date"] == "2026-08-01" for candidate in group["candidates"])

    rejected = api.post(
        f"/ad-accounts/assignment-conflicts/{group['group_id']}/resolve",
        json=_resolve_body(group, winner["id"], policy="reject"),
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "assignment_conflict_budgets_present"
    assert {row.status for row in app.state.ad_account_store.items.values()} == {"active"}
    assert app.state.budget_store.items[UUID(budget.json()["id"])].status == "active"
    assert not [
        row
        for row in app.state.audit_log_store.items
        if row.event_type == "ad_account.assignment_conflict_resolved"
    ]

    resolved = api.post(
        f"/ad-accounts/assignment-conflicts/{group['group_id']}/resolve",
        json=_resolve_body(group, winner["id"], policy="archive", note="Client ownership verified"),
    )
    assert resolved.status_code == 200
    result = resolved.json()
    assert result["status"] == "resolved"
    assert result["winner_account_id"] == winner["id"]
    assert result["loser_account_ids"] == [loser["id"]]
    assert result["archived_budget_ids"] == [budget.json()["id"]]
    assert result["sync_required"] is True
    after_by_id = {candidate["account_id"]: candidate for candidate in result["after"]["candidates"]}
    assert after_by_id[winner["id"]]["account_status"] == "active"
    assert after_by_id[loser["id"]]["account_status"] == "archived"
    assert result["after"]["summary"]["active_candidate_count"] == 1
    assert len(app.state.ad_stats_store.items) == 2
    assert {str(row.spend) for row in app.state.ad_stats_store.items.values()} == {"100.00", "200.00"}
    assert active_assignment_conflict_ids(app.state.ad_account_store) == set()

    history = api.get(f"/budgets/{budget.json()['id']}/history")
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["new_values"]["status"] == "archived"
    audit = [
        row
        for row in app.state.audit_log_store.items
        if row.event_type == "ad_account.assignment_conflict_resolved"
    ]
    assert len(audit) == 1
    assert audit[0].actor_user_id == UUID(admin["id"])
    assert audit[0].event_type == "ad_account.assignment_conflict_resolved"
    assert audit[0].payload["note"] == "Client ownership verified"
    assert audit[0].payload["before"]["summary"]["active_candidate_count"] == 2
    assert audit[0].payload["after"]["summary"]["active_candidate_count"] == 1

    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: [{"date": date_from, "spend": "1.00"}],
    }
    sync = api.post(
        "/ad-accounts/sync/run",
        json={
            "account_ids": [winner["id"]],
            "date_from": "2026-08-02",
            "date_to": "2026-08-02",
            "force": True,
        },
    )
    assert sync.status_code == 200
    assert sync.json()["success"] == 1
    assert sync.json()["jobs"][0]["ad_account_id"] == winner["id"]


def test_stale_group_version_is_rejected_without_partial_changes():
    _reset_api_state()
    _, _, winner, loser = _seed_api_conflict()
    group = api.get("/ad-accounts/assignment-conflicts").json()["items"][0]
    request = _resolve_body(group, winner["id"])
    request["group_version"] = "acv_" + "0" * 64

    stale = api.post(
        f"/ad-accounts/assignment-conflicts/{group['group_id']}/resolve",
        json=request,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assignment_conflict_stale"
    assert app.state.ad_account_store.items[UUID(winner["id"])].status == "active"
    assert app.state.ad_account_store.items[UUID(loser["id"])].status == "active"
    assert app.state.audit_log_store.items == []


def test_agency_can_only_see_and_resolve_groups_fully_bound_to_selected_agency():
    _, admin_token = _reset_api_state()
    first_client, second_client, winner, _ = _seed_api_conflict()
    group = api.get("/ad-accounts/assignment-conflicts").json()["items"][0]
    manager, manager_token = _api_user("agency")
    agency = api.post(
        "/platform/agencies",
        json={"name": "Scoped Resolver", "status": "active", "plan": "starter"},
        headers=_auth_header(admin_token),
    ).json()
    added = api.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": manager["id"], "role": "manager", "status": "active"},
        headers=_auth_header(admin_token),
    )
    assert added.status_code == 200
    bound = api.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": first_client["id"]},
        headers=_auth_header(admin_token),
    )
    assert bound.status_code == 200

    hidden = api.get(
        f"/ad-accounts/assignment-conflicts?agency_id={agency['id']}",
        headers=_auth_header(manager_token),
    )
    assert hidden.status_code == 200
    assert hidden.json()["count"] == 0
    denied = api.post(
        f"/ad-accounts/assignment-conflicts/{group['group_id']}/resolve?agency_id={agency['id']}",
        json=_resolve_body(group, winner["id"]),
        headers=_auth_header(manager_token),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "assignment_conflict_scope_denied"

    second_bound = api.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": second_client["id"]},
        headers=_auth_header(admin_token),
    )
    assert second_bound.status_code == 200
    visible = api.get(
        f"/ad-accounts/assignment-conflicts?agency_id={agency['id']}",
        headers=_auth_header(manager_token),
    )
    assert visible.status_code == 200
    assert visible.json()["count"] == 1


def _sqlite_service_with_conflict(tmp_path):
    db_path = str(tmp_path / f"assignment-conflict-{uuid4()}.db")
    auth_store = SqliteAuthStore(db_path)
    actor = auth_store.create_user(
        UserCreate(email=f"admin-{uuid4()}@sqlite.local", name="Admin", role="admin", status="active")
    )
    client_store = SqliteClientStore(db_path)
    first_client = client_store.create(ClientCreate(name="SQLite First", status="active", default_currency="USD"))
    second_client = client_store.create(ClientCreate(name="SQLite Second", status="active", default_currency="USD"))
    account_store = SqliteAdAccountStore(db_path, client_store)
    first_id, second_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with sqlite_conn(db_path) as conn:
        conn.execute("DROP TRIGGER IF EXISTS trg_ad_accounts_active_assignment_insert")
        conn.execute("DROP TRIGGER IF EXISTS trg_ad_accounts_active_assignment_update")
        conn.executemany(
            """
            INSERT INTO ad_accounts
            (id, client_id, platform, external_account_id, name, currency, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'USD', 'active', ?, ?)
            """,
            [
                (str(first_id), str(first_client.id), "meta", "act_987", "SQLite First", now, now),
                (str(second_id), str(second_client.id), "facebook", "987", "SQLite Second", now, now),
            ],
        )
        conn.commit()
    init_sqlite(db_path)

    stats_store = SqliteAdStatsStore(db_path, account_store)
    budget_store = SqliteBudgetStore(db_path)
    platform_store = SqlitePlatformAdminStore(db_path, auth_store)
    audit_store = SqliteAuditLogStore(db_path)
    service = AssignmentConflictService(
        account_store=account_store,
        client_store=client_store,
        ad_stats_store=stats_store,
        budget_store=budget_store,
        platform_admin_store=platform_store,
        audit_log_store=audit_store,
    )
    return {
        "db_path": db_path,
        "actor": actor,
        "first_client": first_client,
        "second_client": second_client,
        "first_id": first_id,
        "second_id": second_id,
        "account_store": account_store,
        "budget_store": budget_store,
        "audit_store": audit_store,
        "service": service,
    }


def test_sqlite_concurrent_resolution_has_one_winner_and_one_stale_request(tmp_path):
    state = _sqlite_service_with_conflict(tmp_path)
    group = state["service"].list_conflicts(
        actor_user_id=state["actor"].id,
        actor_role="admin",
    ).items[0]
    payload = AssignmentConflictResolveRequest(
        winner_account_id=state["first_id"],
        expected_account_ids=group.account_ids,
        group_version=group.group_version,
        loser_budget_policy="reject",
        note="Concurrent decision",
    )
    barrier = Barrier(2)

    def resolve_once():
        barrier.wait()
        try:
            result = state["service"].resolve_conflict(
                group.group_id,
                payload,
                actor_user_id=state["actor"].id,
                actor_role="admin",
            )
            return 200, result.status
        except HTTPException as exc:
            return exc.status_code, exc.detail["code"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resolve_once) for _ in range(2)]
        outcomes = sorted(future.result() for future in futures)

    assert outcomes == [(200, "resolved"), (409, "assignment_conflict_stale")]
    accounts = state["account_store"].list(status="all")
    assert sum(account.status == "active" for account in accounts) == 1
    assert state["account_store"].get(state["first_id"]).status == "active"
    assert state["account_store"].get(state["second_id"]).status == "archived"
    assert len(state["audit_store"].list(event_type="ad_account.assignment_conflict_resolved")) == 1


def test_sqlite_audit_failure_rolls_back_account_and_budget_archives(tmp_path):
    state = _sqlite_service_with_conflict(tmp_path)
    budget = state["budget_store"].create(
        BudgetCreate(
            client_id=state["second_client"].id,
            scope="account",
            account_id=state["second_id"],
            amount=Decimal("500.00"),
            currency="USD",
            period_type="monthly",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            note="Rollback sentinel",
        )
    )
    group = state["service"].list_conflicts(
        actor_user_id=state["actor"].id,
        actor_role="admin",
    ).items[0]
    payload = AssignmentConflictResolveRequest(
        winner_account_id=state["first_id"],
        expected_account_ids=group.account_ids,
        group_version=group.group_version,
        loser_budget_policy="archive",
        note="This audit is forced to fail",
    )
    with sqlite_conn(state["db_path"]) as conn:
        conn.executescript(
            """
            CREATE TRIGGER fail_assignment_conflict_audit
            BEFORE INSERT ON audit_logs
            WHEN NEW.event_type='ad_account.assignment_conflict_resolved'
            BEGIN
              SELECT RAISE(ABORT, 'forced_audit_failure');
            END;
            """
        )
        conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced_audit_failure"):
        state["service"].resolve_conflict(
            group.group_id,
            payload,
            actor_user_id=state["actor"].id,
            actor_role="admin",
        )

    assert state["account_store"].get(state["first_id"]).status == "active"
    assert state["account_store"].get(state["second_id"]).status == "active"
    persisted_budget = state["budget_store"].get(budget.id)
    assert persisted_budget.status == "active"
    assert persisted_budget.version == budget.version
    assert state["budget_store"].history(budget.id) == []
    assert state["audit_store"].list(event_type="ad_account.assignment_conflict_resolved") == []
