from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.schemas import AdAccountCreate, ClientCreate
from app.services.ad_account_discovery import AdAccountDiscoveryService
from app.services.ad_account_sync import AdAccountSyncService, InMemoryAdAccountSyncJobStore
from app.services.ad_accounts import InMemoryAdAccountStore
from app.services.ad_stats import InMemoryAdStatsStore
from app.services.clients import InMemoryClientStore
from app.services.meta_connection import (
    META_VALIDATION_VERSION,
    MetaConnectionValidationError,
    diagnose_meta_credentials,
    validate_meta_business_connection,
)


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
NOW_EPOCH = int(NOW.timestamp())


class StubResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _validation_get(*, debug_overrides=None, permissions=None, requests=None):
    debug_data = {
        "app_id": "meta-app",
        "application": "Dash Up",
        "data_access_expires_at": NOW_EPOCH + 60 * 24 * 60 * 60,
        "expires_at": NOW_EPOCH + 30 * 24 * 60 * 60,
        "is_valid": True,
        "scopes": ["ads_management", "business_management"],
        "granular_scopes": [{"scope": "ads_management", "target_ids": ["act-1"]}],
        "user_id": "meta-user",
        "configuration_id": "business-config",
    }
    debug_data.update(debug_overrides or {})
    permission_rows = permissions if permissions is not None else [
        {"permission": "ads_management", "status": "granted"},
        {"permission": "business_management", "status": "granted"},
    ]

    def get(url, **kwargs):
        if requests is not None:
            requests.append((url, kwargs))
        if url.endswith("/debug_token"):
            return StubResponse({"data": debug_data})
        return StubResponse({"data": permission_rows})

    return get


def _validate(**overrides):
    values = {
        "access_token": "secret-user-token",
        "app_id": "meta-app",
        "app_secret": "secret-app-token",
        "config_id": "business-config",
        "expected_user_id": "meta-user",
        "token_payload": {"expires_in": 3600},
        "now": NOW,
        "request_get": _validation_get(),
    }
    values.update(overrides)
    return validate_meta_business_connection(**values)


def _validated_credentials(*, expires_at=4_102_444_800):
    return {
        "access_token": "tenant-meta-token",
        "meta_validation_version": META_VALIDATION_VERSION,
        "meta_validated_at": NOW.isoformat(),
        "meta_app_id": "meta-app",
        "meta_business_config_id": "business-config",
        "meta_user_id": "meta-user",
        "meta_granted_permissions": ["ads_management", "business_management"],
        "meta_absolute_expires_at": expires_at,
    }


def _stores():
    clients = InMemoryClientStore()
    accounts = InMemoryAdAccountStore(clients)
    stats = InMemoryAdStatsStore(accounts)
    jobs = InMemoryAdAccountSyncJobStore()
    tenant = clients.create(ClientCreate(name="Meta tenant", default_currency="USD"))
    return clients, accounts, stats, jobs, tenant


def test_business_callback_validation_binds_app_config_user_permissions_and_absolute_expiry():
    requests = []
    metadata = _validate(request_get=_validation_get(requests=requests))

    assert metadata["meta_app_id"] == "meta-app"
    assert metadata["meta_business_config_id"] == "business-config"
    assert metadata["meta_user_id"] == "meta-user"
    assert metadata["meta_absolute_expires_at"] == NOW_EPOCH + 30 * 24 * 60 * 60
    assert set(metadata["meta_granted_permissions"]) == {"ads_management", "business_management"}
    assert requests[0][1]["params"] == {"input_token": "secret-user-token"}
    assert requests[0][1]["headers"] == {"Authorization": "Bearer meta-app|secret-app-token"}
    assert requests[1][1]["headers"] == {"Authorization": "Bearer secret-user-token"}


@pytest.mark.parametrize(
    ("debug_overrides", "code"),
    [
        ({"is_valid": False}, "meta_token_invalid"),
        ({"app_id": "other-app"}, "meta_token_app_mismatch"),
        ({"user_id": "other-user"}, "meta_token_user_mismatch"),
        ({"configuration_id": "other-config"}, "meta_business_config_mismatch"),
        ({"expires_at": NOW_EPOCH - 1}, "meta_token_expired"),
        ({"data_access_expires_at": NOW_EPOCH - 1}, "meta_data_access_expired"),
    ],
)
def test_business_callback_validation_fails_closed(debug_overrides, code):
    with pytest.raises(MetaConnectionValidationError) as error:
        _validate(request_get=_validation_get(debug_overrides=debug_overrides))

    assert error.value.code == code
    assert "secret-user-token" not in str(error.value)
    assert "secret-app-token" not in str(error.value)


def test_business_callback_requires_permissions_in_debug_token_and_grants():
    with pytest.raises(MetaConnectionValidationError) as error:
        _validate(
            required_permissions=("ads_read", "business_management"),
            request_get=_validation_get(
                permissions=[
                    {"permission": "ads_management", "status": "granted"},
                    {"permission": "business_management", "status": "declined"},
                ]
            )
        )

    assert error.value.code == "meta_permissions_missing"


def test_business_callback_accepts_ads_management_as_read_capability_by_default():
    metadata = _validate(
        request_get=_validation_get(
            debug_overrides={"scopes": ["ads_management"]},
            permissions=[{"permission": "ads_management", "status": "granted"}],
        )
    )

    assert metadata["meta_granted_permissions"] == ["ads_management"]


def test_business_callback_validation_converts_transport_failure_to_safe_error():
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("transport included secret-user-token")

    with pytest.raises(MetaConnectionValidationError) as error:
        _validate(request_get=unavailable)

    assert error.value.code == "meta_token_validation_unavailable"
    assert "secret-user-token" not in str(error.value)


def test_business_callback_requires_a_verifiable_absolute_expiry():
    with pytest.raises(MetaConnectionValidationError) as error:
        _validate(
            token_payload={},
            request_get=_validation_get(
                debug_overrides={"expires_at": 0, "data_access_expires_at": 0}
            ),
        )

    assert error.value.code == "meta_token_expiry_missing"


def test_local_diagnostic_marks_legacy_and_expired_connections_for_reconnect():
    legacy = diagnose_meta_credentials({"access_token": "legacy"}, now=NOW)
    expired = diagnose_meta_credentials(
        _validated_credentials(expires_at=NOW_EPOCH - 1),
        now=NOW,
    )

    assert (legacy.status, legacy.code, legacy.reconnect_required) == (
        "unverified",
        "meta_connection_unverified",
        True,
    )
    assert (expired.status, expired.code, expired.reconnect_required) == (
        "error",
        "meta_token_expired",
        True,
    )


def test_local_diagnostic_requires_reconnect_after_app_or_config_rotation():
    app_rotated = diagnose_meta_credentials(
        _validated_credentials(),
        expected_app_id="replacement-app",
        now=NOW,
    )
    config_rotated = diagnose_meta_credentials(
        _validated_credentials(),
        expected_config_id="replacement-config",
        now=NOW,
    )

    assert (app_rotated.code, app_rotated.reconnect_required) == (
        "meta_token_app_mismatch",
        True,
    )
    assert (config_rotated.code, config_rotated.reconnect_required) == (
        "meta_business_config_mismatch",
        True,
    )


def test_meta_discovery_uses_only_selected_agency_credential_and_pins_account():
    clients, accounts, _stats, _jobs, tenant = _stores()
    selected_agency = uuid4()
    other_agency = uuid4()
    selected_id = uuid4()
    candidates = [
        {
            **_validated_credentials(),
            "access_token": "wrong-token",
            "__credential_id": str(uuid4()),
            "__scope_type": "agency",
            "__scope_id": str(other_agency),
        },
        {
            **_validated_credentials(),
            "access_token": "selected-token",
            "__credential_id": str(selected_id),
            "__connection_key": "meta:selected",
            "__scope_type": "agency",
            "__scope_id": str(selected_agency),
        },
    ]
    observed = []
    service = AdAccountDiscoveryService(
        accounts,
        client_store=clients,
        discoverers={
            "meta": lambda credentials: observed.append(credentials)
            or [{"external_account_id": "act_123", "name": "Meta 123", "currency": "USD"}]
        },
        credential_candidates_resolver=lambda *_args: candidates,
    )

    result = service.discover(
        provider="meta",
        client_id=tenant.id,
        user_id=uuid4(),
        agency_id=selected_agency,
        expected_currency="USD",
    )

    assert result.created == 1
    assert observed == [{key: value for key, value in candidates[1].items() if not key.startswith("__")}]
    assert result.items[0].metadata["integration_credential_id"] == str(selected_id)
    assert result.items[0].metadata["meta_connection_status"] == "ready"


def test_meta_sync_never_falls_back_when_bound_credential_is_missing():
    _clients, accounts, stats, jobs, tenant = _stores()
    bound_id = uuid4()
    account = accounts.create(
        AdAccountCreate(
            client_id=tenant.id,
            platform="meta",
            external_account_id="123",
            name="Meta 123",
            currency="USD",
            metadata={"integration_credential_id": str(bound_id)},
        )
    )
    calls = []
    service = AdAccountSyncService(
        accounts,
        jobs,
        stats,
        provider_fetchers={"meta": lambda *_args: calls.append(_args) or []},
        credential_candidates_resolver=lambda *_args: [
            {
                **_validated_credentials(),
                "__credential_id": str(uuid4()),
                "__scope_type": "agency",
                "__scope_id": str(uuid4()),
            }
        ],
    )

    result = service.run_sync(
        account_ids=[account.id],
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 1),
        user_id=uuid4(),
        force=True,
    )

    assert calls == []
    assert result.failed == 1
    assert result.jobs[0].error_code == "provider_reconnect_required"
    assert result.jobs[0].error_category == "auth"
    updated = accounts.get(account.id)
    assert updated.metadata["meta_connection_diagnostic_code"] == "meta_bound_credential_missing"


def test_meta_sync_uses_only_exact_bound_credential_and_strips_internal_fields():
    _clients, accounts, stats, jobs, tenant = _stores()
    bound_id = uuid4()
    account = accounts.create(
        AdAccountCreate(
            client_id=tenant.id,
            platform="meta",
            external_account_id="456",
            name="Meta 456",
            currency="USD",
            metadata={"integration_credential_id": str(bound_id)},
        )
    )
    observed = []
    exact = {
        **_validated_credentials(),
        "access_token": "exact-token",
        "__credential_id": str(bound_id),
        "__connection_key": "meta:exact",
        "__scope_type": "agency",
        "__scope_id": str(uuid4()),
    }
    wrong = {
        **_validated_credentials(),
        "access_token": "wrong-token",
        "__credential_id": str(uuid4()),
        "__scope_type": "agency",
        "__scope_id": str(uuid4()),
    }
    service = AdAccountSyncService(
        accounts,
        jobs,
        stats,
        provider_fetchers={
            "meta": lambda _external, date_from, _date_to, credentials: observed.append(credentials)
            or [{"date": date_from}]
        },
        credential_candidates_resolver=lambda *_args: [wrong, exact],
    )

    result = service.run_sync(
        account_ids=[account.id],
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 1),
        user_id=uuid4(),
        force=True,
    )

    assert result.success == 1
    assert observed == [{key: value for key, value in exact.items() if not key.startswith("__")}]
    assert all(not key.startswith("__") for key in observed[0])
