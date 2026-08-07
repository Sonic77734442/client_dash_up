from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException

from app.services.oauth import InMemoryOAuthStateStore, SqliteOAuthStateStore


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_oauth_state_can_be_consumed_only_once_under_concurrency(kind, tmp_path):
    store = (
        SqliteOAuthStateStore(str(tmp_path / "oauth-state.db"))
        if kind == "sqlite"
        else InMemoryOAuthStateStore()
    )
    created = store.create_state("google", "/sync-monitor", "nonce", ttl_minutes=10)
    barrier = Barrier(2)

    def consume():
        barrier.wait(timeout=5)
        try:
            store.consume_state("google", created.state, "nonce")
            return "consumed"
        except HTTPException as exc:
            return str(exc.detail)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: consume(), range(2)))

    assert results.count("consumed") == 1
    assert results.count("OAuth state already used") == 1
