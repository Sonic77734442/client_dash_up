from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.runtime_db import migrate_postgres
from app.schemas import (
    AdAccountCreate,
    AdAccountPatch,
    BudgetCreate,
    BudgetPatch,
    BudgetTransferRequest,
    ClientCreate,
    ClientPatch,
)
from app.services.ad_accounts import SqliteAdAccountStore
from app.services.budgets import InMemoryBudgetStore, SqliteBudgetStore
from app.services.clients import SqliteClientStore


@pytest.fixture(params=["sqlite", "postgresql"])
def stores(request, monkeypatch, tmp_path):
    backend = request.param
    monkeypatch.setenv("DATABASE_BACKEND", backend)
    if backend == "postgresql":
        url = os.getenv("TEST_DATABASE_URL", "").strip()
        if not url:
            pytest.skip("TEST_DATABASE_URL is required for PostgreSQL regression tests")
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DATABASE_AUTO_MIGRATE", "false")
        migrate_postgres(url)
    path = str(tmp_path / "invariants.sqlite")
    clients = SqliteClientStore(path)
    accounts = SqliteAdAccountStore(path, clients)
    budgets = SqliteBudgetStore(path)
    client = clients.create(ClientCreate(name="Budget invariant test"))
    account = accounts.create(
        AdAccountCreate(
            client_id=client.id, platform="meta", external_account_id=str(uuid4().int), name="Account"
        )
    )
    return clients, accounts, budgets, client, account


def _budget(client_id, *, account_id=None, amount="100", start="2026-09-01", end="2026-09-30"):
    return BudgetCreate(
        client_id=client_id,
        scope="account" if account_id else "client",
        account_id=account_id,
        amount=Decimal(amount),
        currency="USD",
        period_type="custom",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
    )


@pytest.mark.parametrize("scope", ["client", "account"])
@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-08-15", "2026-09-15"),
        ("2026-09-15", "2026-10-15"),
        ("2026-08-01", "2026-10-31"),
        ("2026-09-30", "2026-10-31"),
    ],
)
def test_partial_containing_and_boundary_overlap_is_rejected(stores, scope, start, end):
    _, _, budgets, client, account = stores
    account_id = account.id if scope == "account" else None
    original = budgets.create(_budget(client.id, account_id=account_id))
    with pytest.raises(HTTPException) as caught:
        budgets.create(_budget(client.id, account_id=account_id, start=start, end=end))
    assert caught.value.status_code == 409
    assert [row.id for row in budgets.list(client_id=client.id)] == [original.id]

    adjacent = budgets.create(
        _budget(client.id, account_id=account_id, start="2026-10-01", end="2026-10-31")
    )
    with pytest.raises(HTTPException) as caught:
        budgets.patch(adjacent.id, BudgetPatch(start_date=date(2026, 9, 15)))
    assert caught.value.status_code == 409
    assert budgets.get(adjacent.id).start_date == date(2026, 10, 1)
    assert budgets.history(adjacent.id) == []


def _assert_all_period_caps(budgets, client_id, account_id):
    budgets.create(_budget(client_id, amount="100"))
    budgets.create(_budget(client_id, amount="1000", start="2026-10-01", end="2026-10-31"))
    crossing = _budget(client_id, account_id=account_id, amount="500", start="2026-09-15", end="2026-10-15")
    with pytest.raises(HTTPException) as caught:
        budgets.create(crossing)
    assert caught.value.status_code == 409
    assert "2026-09-01..2026-09-30" in str(caught.value.detail)

    valid = budgets.create(crossing.model_copy(update={"amount": Decimal("100")}))
    with pytest.raises(HTTPException) as caught:
        budgets.patch(valid.id, BudgetPatch(amount=Decimal("101")))
    assert caught.value.status_code == 409
    assert budgets.get(valid.id).amount == Decimal("100")
    assert budgets.history(valid.id) == []


def test_account_create_and_patch_check_every_overlapping_client_cap(stores):
    _, _, budgets, client, account = stores
    _assert_all_period_caps(budgets, client.id, account.id)


def test_inmemory_cap_behavior_matches_persistent_store():
    _assert_all_period_caps(InMemoryBudgetStore(), uuid4(), uuid4())


def test_cross_period_transfer_rolls_back_both_amounts_and_history(stores):
    _, accounts, budgets, client, target_account = stores
    source_account = accounts.create(
        AdAccountCreate(client_id=client.id, platform="meta", external_account_id=str(uuid4().int), name="Source")
    )
    _assert_transfer_cap_rollback(budgets, client.id, source_account.id, target_account.id)


def test_inmemory_cross_period_transfer_rolls_back_both_amounts_and_history():
    _assert_transfer_cap_rollback(InMemoryBudgetStore(), uuid4(), uuid4(), uuid4())


def _assert_transfer_cap_rollback(budgets, client_id, source_account_id, target_account_id):
    budgets.create(_budget(client_id, amount="100"))
    budgets.create(_budget(client_id, amount="1000", start="2026-10-01", end="2026-10-31"))
    target = budgets.create(
        _budget(client_id, account_id=target_account_id, amount="100", start="2026-09-15", end="2026-10-15")
    )
    source = budgets.create(
        _budget(client_id, account_id=source_account_id, amount="500", start="2026-10-01", end="2026-10-31")
    )
    with pytest.raises(HTTPException) as caught:
        budgets.transfer(source.id, BudgetTransferRequest(target_account_id=target_account_id, amount=Decimal("1")))
    assert caught.value.status_code == 409
    assert budgets.get(source.id).amount == source.amount
    assert budgets.get(target.id).amount == target.amount
    assert budgets.history(source.id) == budgets.history(target.id) == []
    assert budgets.list_transfers(source.id) == []


@pytest.mark.parametrize("resource", ["client", "account"])
@pytest.mark.parametrize("competing_edit", ["archive", "rename"])
def test_partial_patch_preserves_a_concurrent_committed_change(stores, monkeypatch, resource, competing_edit):
    clients, accounts, _, client, account = stores
    store, row = (clients, client) if resource == "client" else (accounts, account)
    payload = ClientPatch(notes="new note") if resource == "client" else AdAccountPatch(metadata={"annotation": "new note"})
    main_thread = threading.current_thread()
    read_done = threading.Event()
    resume_patch = threading.Event()
    original_get = store.get

    def paused_get(row_id):
        snapshot = original_get(row_id)
        if threading.current_thread() is not main_thread:
            read_done.set()
            assert resume_patch.wait(10)
        return snapshot

    monkeypatch.setattr(store, "get", paused_get)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(store.patch, row.id, payload)
        try:
            assert read_done.wait(10)
            if competing_edit == "archive":
                assert store.archive(row.id).status == "archived"
            else:
                rename = ClientPatch(name="Renamed") if resource == "client" else AdAccountPatch(name="Renamed")
                store.patch(row.id, rename)
        finally:
            resume_patch.set()
        updated = pending.result(timeout=10)
    current = store.get(row.id)
    if competing_edit == "archive":
        assert current.status == updated.status == "archived"
    else:
        assert current.name == updated.name == "Renamed"
    if resource == "client":
        assert current.notes == "new note"
    else:
        assert current.metadata == {"annotation": "new note"}


def test_postgres_final_exclusion_guard_returns_conflict_and_rolls_back(stores, monkeypatch):
    if os.getenv("DATABASE_BACKEND") != "postgresql":
        pytest.skip("PostgreSQL exclusion constraint test")
    _, _, budgets, client, _ = stores
    existing = budgets.create(_budget(client.id))
    # Exercise the database's last line of defense independently of the app
    # precheck, as needed for external writers sharing the database.
    monkeypatch.setattr(budgets, "_assert_no_overlap", lambda **kwargs: None)
    with pytest.raises(HTTPException) as caught:
        budgets.create(_budget(client.id, start="2026-09-15", end="2026-10-15"))
    assert caught.value.status_code == 409
    assert [row.id for row in budgets.list(client_id=client.id)] == [existing.id]
