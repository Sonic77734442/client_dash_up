from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.schemas import AgencyMemberCreate, IntegrationCredentialCreate, UserPatch
from app.services.provider_budget_commands import (
    BudgetConflictError,
    BudgetField,
    BudgetSnapshot,
    BudgetTargetType,
    ProviderWriteReceipt,
)
from app.services.providers.meta_budget import (
    MetaBudgetTargetFieldSnapshot,
    MetaBudgetTargetSnapshot,
)


client = TestClient(app)


class FakeMetaBudgetAdapter:
    def __init__(self, *, current_minor: int = 1000):
        self.current_minor = current_minor
        self.read_calls = 0
        self.post_calls = 0
        self.write_entered: threading.Event | None = None
        self.write_release: threading.Event | None = None
        self.apply_write = True
        self.target_type = BudgetTargetType.CAMPAIGN
        self.target_id = "777"
        self.target_field = BudgetField.DAILY_BUDGET
        self._guard = threading.Lock()

    def read_budget(self, request):
        with self._guard:
            self.read_calls += 1
            current = self.current_minor
        return BudgetSnapshot(
            target_type=request.target_type,
            provider_target_id=request.provider_target_id,
            provider_account_id=request.provider_account_id,
            field=request.field,
            amount_minor=current,
            currency=request.currency,
            observed_at=datetime.now(timezone.utc),
            provider_trace_id=f"read-{self.read_calls}",
        )

    def write_budget(self, request):
        with self._guard:
            self.post_calls += 1
        if self.write_entered is not None:
            self.write_entered.set()
        if self.write_release is not None:
            assert self.write_release.wait(timeout=5)
        if self.apply_write:
            with self._guard:
                self.current_minor = request.amount_minor
        return ProviderWriteReceipt(accepted=True, provider_trace_id="write-1")

    def list_budget_targets(self, provider_account_id):
        assert provider_account_id == "123456"
        return [
            MetaBudgetTargetSnapshot(
                target_type=self.target_type,
                provider_target_id=self.target_id,
                name="Always-on",
                status="ACTIVE",
                budget_fields=(
                    MetaBudgetTargetFieldSnapshot(
                        field=self.target_field,
                        current_minor=self.current_minor,
                        currency="USD",
                        observed_at=datetime.now(timezone.utc),
                    ),
                ),
            )
        ]

    def verify_budget_target(self, request):
        target = next(
            (
                item
                for item in self.list_budget_targets(request.provider_account_id)
                if item.target_type == request.target_type
                and item.provider_target_id == request.provider_target_id
            ),
            None,
        )
        if target is None or not any(
            field.field == request.field and field.editable for field in target.budget_fields
        ):
            from app.services.provider_budget_commands import BudgetConflictError

            raise BudgetConflictError(
                "meta_budget_target_not_editable",
                "The selected Meta target is not editable",
            )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _issue(user_id: str) -> str:
    response = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": user_id, "ttl_minutes": 60},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "true")
    monkeypatch.setenv("META_BUDGET_PREVIEW_SECRET", "budget-preview-signing-secret-32-bytes-minimum")
    monkeypatch.setenv("META_BUDGET_PREVIEW_TTL_SECONDS", "300")
    monkeypatch.setenv("FACEBOOK_CLIENT_ID", "meta-app")
    monkeypatch.setenv("FACEBOOK_CLIENT_SECRET", "meta-app-secret")
    monkeypatch.setenv("FACEBOOK_LOGIN_CONFIG_ID", "business-config")
    monkeypatch.delenv("META_BUDGET_SPEND_CAP_ENABLED", raising=False)
    monkeypatch.delenv("META_BUDGET_ADMIN_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("META_BUDGET_ALLOWED_CLIENT_IDS", raising=False)
    monkeypatch.delenv("META_BUDGET_ALLOW_ALL_CLIENTS", raising=False)


def _reset_and_provision(monkeypatch, *, bind: bool = True):
    _configure(monkeypatch)
    client.cookies.clear()
    client.headers.clear()
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "budget-admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    ).json()
    admin_token = _issue(admin["id"])
    tenant_response = client.post(
        "/clients",
        json={"name": "Budget tenant", "status": "active", "default_currency": "USD"},
        headers=_headers(admin_token),
    )
    assert tenant_response.status_code == 200, tenant_response.text
    tenant = tenant_response.json()
    monkeypatch.setenv("META_BUDGET_ALLOWED_CLIENT_IDS", tenant["id"])
    account_response = client.post(
        "/ad-accounts",
        json={
            "client_id": tenant["id"],
            "platform": "meta",
            "external_account_id": "act_123456",
            "name": "Meta 123456",
            "currency": "USD",
            "status": "active",
        },
        headers=_headers(admin_token),
    )
    assert account_response.status_code == 200, account_response.text
    account = account_response.json()
    owner = client.post(
        "/auth/internal/users",
        json={
            "email": "budget-owner@test.local",
            "name": "Owner",
            "role": "solo_client",
            "status": "active",
        },
    ).json()
    access = client.post(
        "/auth/internal/access",
        json={"user_id": owner["id"], "client_id": tenant["id"], "role": "client"},
    )
    assert access.status_code == 200, access.text
    owner_token = _issue(owner["id"])
    credential = app.state.integration_credential_store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=UUID(tenant["id"]),
            connection_key="meta:budget-owner",
            created_by=UUID(owner["id"]),
            credentials={
                "access_token": "tenant-meta-token",
                "meta_validation_version": 1,
                "meta_validated_at": "2026-08-18T12:00:00+00:00",
                "meta_app_id": "meta-app",
                "meta_business_config_id": "business-config",
                "meta_user_id": "meta-user",
                "meta_granted_permissions": ["ads_management"],
                "meta_absolute_expires_at": 4_102_444_800,
            },
        )
    )
    if bind:
        app.state.integration_credential_store.bind_provider_account(
            ad_account_id=UUID(account["id"]),
            provider="meta",
            external_account_id="123456",
            credential_id=credential.id,
            bound_by=UUID(owner["id"]),
        )
    adapter = FakeMetaBudgetAdapter()
    app.state.meta_budget_adapter_factory = lambda _credentials: adapter
    return {
        "admin": admin,
        "admin_token": admin_token,
        "tenant": tenant,
        "account": account,
        "owner": owner,
        "owner_token": owner_token,
        "credential": credential,
        "adapter": adapter,
    }


def _provision_agency_roles(setup):
    agency_response = client.post(
        "/platform/agencies",
        json={"name": "Budget agency", "slug": f"budget-{uuid4().hex[:8]}"},
        headers=_headers(setup["admin_token"]),
    )
    assert agency_response.status_code == 200, agency_response.text
    agency = agency_response.json()
    users = {}
    for role in ("owner", "manager", "member"):
        user_response = client.post(
            "/auth/internal/users",
            json={
                "email": f"budget-agency-{role}-{uuid4().hex[:8]}@test.local",
                "name": f"Agency {role}",
                "role": "agency",
                "status": "active",
            },
        )
        assert user_response.status_code == 200, user_response.text
        user = user_response.json()
        member_response = client.post(
            f"/platform/agencies/{agency['id']}/members",
            json={"user_id": user["id"], "role": role, "status": "active"},
            headers=_headers(setup["admin_token"]),
        )
        assert member_response.status_code == 200, member_response.text
        users[role] = {**user, "token": _issue(user["id"])}
    access_response = client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": setup["tenant"]["id"]},
        headers=_headers(setup["admin_token"]),
    )
    assert access_response.status_code == 200, access_response.text
    credential = app.state.integration_credential_store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="agency",
            scope_id=UUID(agency["id"]),
            connection_key=f"meta:agency:{agency['id']}",
            created_by=UUID(users["owner"]["id"]),
            credentials=dict(setup["credential"].credentials),
        )
    )
    app.state.integration_credential_store.bind_provider_account(
        ad_account_id=UUID(setup["account"]["id"]),
        provider="meta",
        external_account_id="123456",
        credential_id=credential.id,
        bound_by=UUID(users["owner"]["id"]),
    )
    return {"agency": agency, "users": users, "credential": credential}


def _change_payload(
    setup,
    *,
    amount_minor: int = 2500,
    expected_current_minor: int = 1000,
) -> dict:
    return {
        "client_id": setup["tenant"]["id"],
        "ad_account_id": setup["account"]["id"],
        "target_type": "campaign",
        "provider_target_id": "777",
        "field": "daily_budget",
        "amount_minor": amount_minor,
        "expected_current_minor": expected_current_minor,
        "currency": "USD",
        "reason": "Approved pacing correction",
    }


def _preview(setup, *, amount_minor: int = 2500, expected_current_minor: int = 1000) -> dict:
    response = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(
            setup,
            amount_minor=amount_minor,
            expected_current_minor=expected_current_minor,
        ),
        headers=_headers(setup["owner_token"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _revoke_after_execution_read(setup, revoke) -> None:
    """Run one deterministic mutation after the last provider GET and before POST."""

    adapter = setup["adapter"]
    original_read = adapter.read_budget
    revoked = False

    def read_then_revoke(request):
        nonlocal revoked
        snapshot = original_read(request)
        # Read 1 created the preview. Read 2 is the execution preflight. The
        # live authorization callback must run after this hook and stop POST.
        if adapter.read_calls == 2 and not revoked:
            revoked = True
            revoke()
        return snapshot

    adapter.read_budget = read_then_revoke


def test_readiness_targets_preview_confirm_and_history_use_server_owned_binding(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    readiness = client.get(
        "/provider-controls/meta/readiness",
        params={"client_id": setup["tenant"]["id"], "ad_account_id": setup["account"]["id"]},
        headers=_headers(setup["owner_token"]),
    )
    assert readiness.status_code == 200
    assert readiness.json()["can_confirm"] is True
    assert readiness.json()["can_reconcile"] is True
    assert readiness.json()["credential_ready"] is True

    targets = client.get(
        f"/provider-controls/meta/accounts/{setup['account']['id']}/budget-targets",
        headers=_headers(setup["owner_token"]),
    )
    assert targets.status_code == 200, targets.text
    assert targets.json()["items"][0]["provider_target_id"] == "777"

    preview = _preview(setup)
    assert preview["current_minor"] == 1000
    assert preview["requested_minor"] == 2500
    assert "credential_id" not in preview["request"]
    assert "provider_account_id" not in preview["request"]
    assert setup["adapter"].post_calls == 0

    confirmed = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-api-0001"},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["command"]["status"] == "applied"
    assert body["command"]["confirmed_after_minor"] == 2500
    public_command = json.dumps(body["command"], sort_keys=True)
    assert "credential_snapshot" not in public_command
    assert "authorization_snapshot" not in public_command
    assert "credential_id" not in public_command
    assert "meta_user_id" not in public_command
    assert setup["adapter"].post_calls == 1
    persisted = app.state.provider_budget_command_store.get(UUID(body["command"]["id"]))
    final_attempt = persisted.attempts[-1]
    assert final_attempt.authorization_snapshot.feature_enabled is True
    assert final_attempt.authorization_snapshot.client_rollout_allowed is True
    assert final_attempt.authorization_snapshot.binding_verified is True
    assert final_attempt.authorization_snapshot.client_active is True
    assert final_attempt.authorization_snapshot.account_active is True
    assert final_attempt.authorization_snapshot.provider_account_id == "123456"
    security = app.state.integration_credential_store.get_security_status(setup["credential"].id)
    assert final_attempt.credential_snapshot.revision == security.semantic_revision

    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "false")
    app.state.integration_credential_store.archive(setup["credential"].id)
    viewer = client.post(
        "/auth/internal/users",
        json={"email": "budget-viewer@test.local", "name": "Viewer", "role": "client", "status": "active"},
    ).json()
    assert client.post(
        "/auth/internal/access",
        json={"user_id": viewer["id"], "client_id": setup["tenant"]["id"], "role": "client"},
    ).status_code == 200
    history = client.get(
        "/provider-controls/meta/budget-changes",
        params={"client_id": setup["tenant"]["id"]},
        headers=_headers(_issue(viewer["id"])),
    )
    assert history.status_code == 200, history.text
    assert history.json()["count"] == 1
    assert history.json()["items"][0]["id"] == body["command"]["id"]


def test_reconnect_same_credential_uuid_invalidates_old_preview_without_meta_post(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    preview = _preview(setup)
    original_id = setup["credential"].id
    reconnected = app.state.integration_credential_store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=UUID(setup["tenant"]["id"]),
            connection_key="meta:budget-owner",
            created_by=UUID(setup["owner"]["id"]),
            credentials=dict(setup["credential"].credentials),
        )
    )
    assert reconnected.id == original_id
    app.state.integration_credential_store.bind_provider_account(
        ad_account_id=UUID(setup["account"]["id"]),
        provider="meta",
        external_account_id="123456",
        credential_id=reconnected.id,
        bound_by=UUID(setup["owner"]["id"]),
    )

    response = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "same-uuid-reconnect-1"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "preview_credential_changed"
    assert setup["adapter"].post_calls == 0


def test_client_is_read_only_and_confirm_requires_literal_true(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    viewer = client.post(
        "/auth/internal/users",
        json={"email": "readonly@test.local", "name": "Viewer", "role": "client", "status": "active"},
    ).json()
    assert client.post(
        "/auth/internal/access",
        json={"user_id": viewer["id"], "client_id": setup["tenant"]["id"], "role": "client"},
    ).status_code == 200
    viewer_token = _issue(viewer["id"])
    denied = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(setup),
        headers=_headers(viewer_token),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "client_read_only"

    preview = _preview(setup)
    base = {**_change_payload(setup), "preview_token": preview["preview_token"]}
    missing = client.post(
        "/provider-controls/meta/budget-changes",
        json=base,
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-api-0002"},
    )
    false_confirm = client.post(
        "/provider-controls/meta/budget-changes",
        json={**base, "confirm": False},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-api-0003"},
    )
    assert missing.status_code == 422
    assert false_confirm.status_code == 422
    assert setup["adapter"].post_calls == 0


def test_feature_and_invalid_ttl_fail_before_adapter_or_provider_post(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "false")
    disabled = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": "x" * 80, "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-api-0004"},
    )
    assert disabled.status_code == 503
    assert disabled.json()["error"]["code"] == "meta_budget_controls_disabled"
    assert setup["adapter"].post_calls == 0


def test_preview_rejects_noop_budget_change_without_provider_post(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    response = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(setup, amount_minor=1000),
        headers=_headers(setup["owner_token"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "provider_budget_no_change"
    assert setup["adapter"].post_calls == 0

    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "true")
    monkeypatch.setenv("META_BUDGET_PREVIEW_TTL_SECONDS", "not-an-integer")
    readiness = client.get(
        "/provider-controls/meta/readiness",
        params={"client_id": setup["tenant"]["id"], "ad_account_id": setup["account"]["id"]},
        headers=_headers(setup["owner_token"]),
    )
    assert readiness.status_code == 200
    assert readiness.json()["reason_code"] == "meta_budget_preview_ttl_invalid"
    preview = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(setup),
        headers=_headers(setup["owner_token"]),
    )
    assert preview.status_code == 503
    assert preview.json()["error"]["code"] == "meta_budget_preview_ttl_invalid"
    assert setup["adapter"].post_calls == 0


def test_missing_explicit_binding_is_rediscovery_required_and_stale_metadata_is_ignored(monkeypatch):
    setup = _reset_and_provision(monkeypatch, bind=False)
    # A forged/historical metadata marker cannot create a money-write binding.
    app.state.ad_account_store.patch(
        UUID(setup["account"]["id"]),
        main_module.AdAccountPatch(
            metadata={
                "integration_credential_id": str(setup["credential"].id),
                "meta_connection_status": "ready",
                "meta_rediscovery_required": False,
            }
        ),
    )
    readiness = client.get(
        "/provider-controls/meta/readiness",
        params={"client_id": setup["tenant"]["id"], "ad_account_id": setup["account"]["id"]},
        headers=_headers(setup["owner_token"]),
    )
    assert readiness.status_code == 200
    assert readiness.json()["binding_ready"] is False
    assert readiness.json()["reason_code"] == "meta_budget_rediscovery_required"
    assert app.state.integration_credential_store.resolve_bound_credential(
        ad_account_id=UUID(setup["account"]["id"]),
        provider="meta",
        external_account_id="123456",
    ) is None


def test_binding_is_rechecked_after_queue_and_before_provider_write(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    preview = _preview(setup)
    original_acquire = main_module._acquire_meta_budget_target_lock

    def acquire_then_revoke(command):
        token = original_acquire(command)
        assert token is not None
        app.state.integration_credential_store.remove_provider_account_binding(
            ad_account_id=UUID(setup["account"]["id"]),
            provider="meta",
        )
        return token

    monkeypatch.setattr(main_module, "_acquire_meta_budget_target_lock", acquire_then_revoke)
    response = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-api-0005"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "meta_budget_rediscovery_required"
    assert setup["adapter"].post_calls == 0


@pytest.mark.parametrize(
    ("revocation", "expected_code"),
    [
        ("archive_credential", "meta_budget_rediscovery_required"),
        ("remove_binding", "meta_budget_rediscovery_required"),
        ("deactivate_client", "meta_budget_scope_inactive"),
        ("deactivate_account", "meta_budget_scope_inactive"),
        ("disable_feature", "meta_budget_controls_disabled"),
        ("revoke_rollout", "meta_budget_client_not_allowed"),
        ("deactivate_actor", "meta_budget_actor_revoked"),
        ("revoke_session", "meta_budget_session_revoked"),
    ],
)
def test_final_live_reauthorization_blocks_revocation_during_provider_reads(
    monkeypatch,
    revocation,
    expected_code,
):
    setup = _reset_and_provision(monkeypatch)
    preview = _preview(setup)

    def revoke():
        if revocation == "archive_credential":
            app.state.integration_credential_store.archive(setup["credential"].id)
        elif revocation == "remove_binding":
            app.state.integration_credential_store.remove_provider_account_binding(
                ad_account_id=UUID(setup["account"]["id"]),
                provider="meta",
            )
        elif revocation == "deactivate_client":
            app.state.client_store.patch(
                UUID(setup["tenant"]["id"]),
                main_module.ClientPatch(status="inactive"),
            )
        elif revocation == "deactivate_account":
            app.state.ad_account_store.patch(
                UUID(setup["account"]["id"]),
                main_module.AdAccountPatch(status="inactive"),
            )
        elif revocation == "disable_feature":
            monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "false")
        elif revocation == "revoke_rollout":
            monkeypatch.setenv("META_BUDGET_ALLOWED_CLIENT_IDS", "")
        elif revocation == "deactivate_actor":
            app.state.auth_store.patch_user(
                UUID(setup["owner"]["id"]),
                UserPatch(status="inactive"),
            )
        elif revocation == "revoke_session":
            app.state.auth_store.revoke_session(setup["owner_token"])
        else:  # pragma: no cover - parameter list is closed above.
            raise AssertionError(f"Unknown revocation: {revocation}")

    _revoke_after_execution_read(setup, revoke)
    response = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": f"budget-live-revoke-{revocation}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["command"]["status"] == "failed"
    assert response.json()["command"]["error"]["code"] == expected_code
    assert setup["adapter"].post_calls == 0
    durable = app.state.provider_budget_command_store.get(
        UUID(response.json()["command"]["id"])
    )
    assert durable is not None
    failure_snapshot = durable.attempts[-1].authorization_snapshot
    assert failure_snapshot is not None
    assert failure_snapshot.final_gate_verified is False
    assert failure_snapshot.final_gate_failure_code == expected_code
    # The callback did not produce a trusted live context, so preview-time
    # mutable values must not be presented as final-gate facts.
    assert failure_snapshot.actor_role is None
    assert failure_snapshot.selected_agency_id is None
    assert failure_snapshot.agency_member_role is None
    assert failure_snapshot.agency_active is False
    assert failure_snapshot.can_admin_provider_write is False
    assert failure_snapshot.can_manage_spend_cap is False
    assert failure_snapshot.has_client_access is False
    assert failure_snapshot.agency_client_bound is False
    assert failure_snapshot.solo_client_owned is False
    assert failure_snapshot.client_active is None
    assert failure_snapshot.account_active is None
    assert failure_snapshot.binding_verified is None
    assert failure_snapshot.provider_account_id is None
    assert failure_snapshot.spend_cap_enabled is None
    failure_attempt = durable.attempts[-1]
    assert failure_attempt.credential_id is None
    credential_snapshot = failure_attempt.credential_snapshot
    assert credential_snapshot is not None
    assert credential_snapshot.revision == 0
    assert credential_snapshot.meta_app_id == ""
    assert credential_snapshot.meta_business_config_id == ""
    assert credential_snapshot.meta_user_id == ""
    assert credential_snapshot.granted_permissions == ()
    assert credential_snapshot.validated_at is None
    assert credential_snapshot.final_gate_verified is False
    assert credential_snapshot.final_gate_failure_code == expected_code


def test_spend_cap_switch_off_during_provider_read_blocks_post_and_is_audited(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    monkeypatch.setenv("META_BUDGET_SPEND_CAP_ENABLED", "true")
    agency_setup = _provision_agency_roles(setup)
    agency_id = agency_setup["agency"]["id"]
    owner = agency_setup["users"]["owner"]
    manager = agency_setup["users"]["manager"]
    setup["adapter"].target_type = BudgetTargetType.ACCOUNT
    setup["adapter"].target_id = "123456"
    setup["adapter"].target_field = BudgetField.SPEND_CAP
    payload = {
        "client_id": setup["tenant"]["id"],
        "ad_account_id": setup["account"]["id"],
        "agency_id": agency_id,
        "target_type": "account",
        "provider_target_id": "123456",
        "field": "spend_cap",
        "amount_minor": 2500,
        "expected_current_minor": 1000,
        "currency": "USD",
        "reason": "Approved account limit correction",
    }

    manager_preview = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=payload,
        headers=_headers(manager["token"]),
    )
    assert manager_preview.status_code == 403, manager_preview.text
    assert manager_preview.json()["error"]["code"] == "spend_cap_forbidden"

    preview_response = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=payload,
        headers=_headers(owner["token"]),
    )
    assert preview_response.status_code == 200, preview_response.text

    _revoke_after_execution_read(
        setup,
        lambda: monkeypatch.setenv("META_BUDGET_SPEND_CAP_ENABLED", "false"),
    )
    response = client.post(
        "/provider-controls/meta/budget-changes",
        json={
            **payload,
            "preview_token": preview_response.json()["preview_token"],
            "confirm": True,
        },
        headers={
            **_headers(owner["token"]),
            "Idempotency-Key": "budget-live-disable-spend-cap",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["command"]["status"] == "failed"
    assert response.json()["command"]["error"]["code"] == "meta_spend_cap_disabled"
    assert setup["adapter"].post_calls == 0
    durable = app.state.provider_budget_command_store.get(
        UUID(response.json()["command"]["id"])
    )
    assert durable is not None
    attempt = durable.attempts[-1]
    assert attempt.authorization_snapshot is not None
    assert attempt.authorization_snapshot.spend_cap_enabled is False
    assert attempt.authorization_snapshot.can_manage_spend_cap is False
    assert attempt.authorization_snapshot.final_gate_verified is False
    assert attempt.authorization_snapshot.final_gate_failure_code == "meta_spend_cap_disabled"
    assert attempt.credential_snapshot is not None
    assert attempt.credential_snapshot.final_gate_verified is False
    assert attempt.credential_snapshot.final_gate_failure_code == "meta_spend_cap_disabled"


def test_final_live_reauthorization_blocks_agency_manage_revocation_during_provider_reads(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    agency_setup = _provision_agency_roles(setup)
    agency_id = agency_setup["agency"]["id"]
    manager = agency_setup["users"]["manager"]
    payload = {**_change_payload(setup), "agency_id": agency_id}
    preview_response = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=payload,
        headers=_headers(manager["token"]),
    )
    assert preview_response.status_code == 200, preview_response.text

    def revoke_manage_role():
        app.state.platform_admin_store.upsert_member(
            UUID(agency_id),
            AgencyMemberCreate(
                user_id=UUID(manager["id"]),
                role="member",
                status="active",
            ),
            actor_user_id=UUID(setup["admin"]["id"]),
            actor_is_platform_admin=True,
        )

    _revoke_after_execution_read(setup, revoke_manage_role)
    response = client.post(
        "/provider-controls/meta/budget-changes",
        json={
            **payload,
            "preview_token": preview_response.json()["preview_token"],
            "confirm": True,
        },
        headers={
            **_headers(manager["token"]),
            "Idempotency-Key": "budget-live-revoke-agency-manager",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["command"]["status"] == "failed"
    assert response.json()["command"]["error"]["code"] == "agency_manage_forbidden"
    assert setup["adapter"].post_calls == 0


def test_lost_execution_fence_stops_before_meta_post(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    preview = _preview(setup)

    def reject_lost_fence(_command, _lease_token):
        raise BudgetConflictError(
            "meta_budget_target_lock_lost",
            "The execution lease was lost",
        )

    monkeypatch.setattr(main_module, "_renew_meta_budget_target_lock", reject_lost_fence)
    response = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-fence-lost-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["command"]["status"] == "conflict"
    assert response.json()["command"]["error"]["code"] == "meta_budget_target_lock_lost"
    assert setup["adapter"].post_calls == 0


def test_concurrent_commands_for_same_target_allow_at_most_one_provider_post(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    first_preview = _preview(setup, amount_minor=2000)
    second_preview = _preview(setup, amount_minor=3000)
    setup["adapter"].write_entered = threading.Event()
    setup["adapter"].write_release = threading.Event()
    responses = {}

    def confirm_first():
        local = TestClient(app)
        responses["first"] = local.post(
            "/provider-controls/meta/budget-changes",
            json={
                **_change_payload(setup, amount_minor=2000),
                "preview_token": first_preview["preview_token"],
                "confirm": True,
            },
            headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-concurrent-1"},
        )

    thread = threading.Thread(target=confirm_first)
    thread.start()
    assert setup["adapter"].write_entered.wait(timeout=5)
    second = client.post(
        "/provider-controls/meta/budget-changes",
        json={
            **_change_payload(setup, amount_minor=3000),
            "preview_token": second_preview["preview_token"],
            "confirm": True,
        },
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-concurrent-2"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "meta_budget_target_busy"
    setup["adapter"].write_release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses["first"].status_code == 200, responses["first"].text
    assert setup["adapter"].post_calls == 1


def test_reconcile_unknown_command_is_provider_read_only(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    setup["adapter"].apply_write = False
    preview = _preview(setup)
    second_preview = _preview(setup, amount_minor=3000)
    confirmed = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-reconcile-1"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["command"]["status"] == "unknown"
    assert setup["adapter"].post_calls == 1

    blocked_preview = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(setup, amount_minor=3000),
        headers=_headers(setup["owner_token"]),
    )
    assert blocked_preview.status_code == 409, blocked_preview.text
    assert blocked_preview.json()["error"]["code"] == "meta_budget_target_quarantined"
    blocked_confirmation = client.post(
        "/provider-controls/meta/budget-changes",
        json={
            **_change_payload(setup, amount_minor=3000),
            "preview_token": second_preview["preview_token"],
            "confirm": True,
        },
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-reconcile-2"},
    )
    assert blocked_confirmation.status_code == 409, blocked_confirmation.text
    assert blocked_confirmation.json()["error"]["code"] == "meta_budget_target_quarantined"
    assert setup["adapter"].post_calls == 1

    # Simulate provider eventual consistency after the ambiguous read-back.
    setup["adapter"].current_minor = 2500
    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "false")
    recovery_readiness = client.get(
        "/provider-controls/meta/readiness",
        params={"client_id": setup["tenant"]["id"], "ad_account_id": setup["account"]["id"]},
        headers=_headers(setup["owner_token"]),
    )
    assert recovery_readiness.status_code == 200
    assert recovery_readiness.json()["feature_enabled"] is False
    assert recovery_readiness.json()["can_preview"] is False
    assert recovery_readiness.json()["can_confirm"] is False
    assert recovery_readiness.json()["can_reconcile"] is True
    reconciled = client.post(
        f"/provider-controls/meta/budget-changes/{confirmed.json()['command']['id']}/reconcile",
        headers=_headers(setup["owner_token"]),
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "applied"
    assert reconciled.json()["attempts"][-1]["reconciliation"] is True
    persisted = app.state.provider_budget_command_store.get(UUID(reconciled.json()["id"]))
    assert persisted.attempts[-1].actor_user_id == UUID(setup["owner"]["id"])
    assert persisted.attempts[-1].credential_id == setup["credential"].id
    assert setup["adapter"].post_calls == 1

    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "true")
    after_reconciliation = _preview(
        setup,
        amount_minor=3000,
        expected_current_minor=2500,
    )
    assert after_reconciliation["current_minor"] == 2500


def test_preview_token_cannot_create_two_commands(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    preview = _preview(setup)
    body = {**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True}
    first = client.post(
        "/provider-controls/meta/budget-changes",
        json=body,
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-preview-once-1"},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/provider-controls/meta/budget-changes",
        json=body,
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-preview-once-2"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "preview_already_consumed"
    assert setup["adapter"].post_calls == 1


def test_exact_target_type_is_proved_before_preview(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    setup["adapter"].target_type = BudgetTargetType.AD_SET
    setup["adapter"].target_id = "888"
    spoofed = {
        **_change_payload(setup),
        "target_type": "campaign",
        "provider_target_id": "888",
    }
    response = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=spoofed,
        headers=_headers(setup["owner_token"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "meta_budget_target_not_editable"
    assert setup["adapter"].post_calls == 0


def test_budget_rollout_allowlist_is_default_deny_and_malformed_values_fail_closed(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    monkeypatch.setenv("META_BUDGET_ALLOWED_CLIENT_IDS", "")
    readiness = client.get(
        "/provider-controls/meta/readiness",
        params={"client_id": setup["tenant"]["id"], "ad_account_id": setup["account"]["id"]},
        headers=_headers(setup["owner_token"]),
    )
    assert readiness.status_code == 200
    assert readiness.json()["can_confirm"] is False
    assert readiness.json()["reason_code"] == "meta_budget_client_not_allowed"
    denied = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(setup),
        headers=_headers(setup["owner_token"]),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "meta_budget_client_not_allowed"

    monkeypatch.setenv("META_BUDGET_ALLOWED_CLIENT_IDS", "not-a-uuid")
    malformed = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(setup),
        headers=_headers(setup["owner_token"]),
    )
    assert malformed.status_code == 503
    assert malformed.json()["error"]["code"] == "meta_budget_rollout_configuration_invalid"
    assert setup["adapter"].post_calls == 0

    monkeypatch.setenv("META_BUDGET_ALLOWED_CLIENT_IDS", setup["tenant"]["id"])
    allowed = _preview(setup)
    assert allowed["current_minor"] == 1000


def test_aged_unknown_can_be_resolved_read_only_and_releases_quarantine(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    setup["adapter"].apply_write = False
    preview = _preview(setup)
    confirmed = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-resolve-unknown-1"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["command"]["status"] == "unknown"
    command_id = UUID(confirmed.json()["command"]["id"])
    command = app.state.provider_budget_command_store._items[command_id]
    app.state.provider_budget_command_store._items[command_id] = replace(
        command,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=601),
    )

    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "false")
    resolved = client.post(
        f"/provider-controls/meta/budget-changes/{command_id}/resolve-unknown",
        json={"confirm": True, "resolution": "accept_current_state"},
        headers=_headers(setup["owner_token"]),
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "conflict"
    assert resolved.json()["attempts"][-1]["reconciliation"] is True
    assert resolved.json()["attempts"][-1]["confirmed_after_minor"] == 1000
    assert setup["adapter"].post_calls == 1

    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "true")
    next_preview = _preview(setup, amount_minor=2000)
    assert next_preview["current_minor"] == 1000


def test_reconcile_uses_current_exact_bound_credential_after_reconnect(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    setup["adapter"].apply_write = False
    preview = _preview(setup)
    confirmed = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-reconnect-reconcile-1"},
    )
    assert confirmed.status_code == 200
    original_credential_id = setup["credential"].id
    app.state.integration_credential_store.archive(original_credential_id)
    replacement = app.state.integration_credential_store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=UUID(setup["tenant"]["id"]),
            connection_key="meta:budget-owner-replacement",
            created_by=UUID(setup["owner"]["id"]),
            credentials=dict(setup["credential"].credentials),
        )
    )
    assert replacement.id != original_credential_id
    app.state.integration_credential_store.bind_provider_account(
        ad_account_id=UUID(setup["account"]["id"]),
        provider="meta",
        external_account_id="123456",
        credential_id=replacement.id,
        bound_by=UUID(setup["owner"]["id"]),
    )
    setup["adapter"].current_minor = 2500
    monkeypatch.setenv("META_BUDGET_COMMANDS_ENABLED", "false")
    reconciled = client.post(
        f"/provider-controls/meta/budget-changes/{confirmed.json()['command']['id']}/reconcile",
        headers=_headers(setup["owner_token"]),
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "applied"
    persisted = app.state.provider_budget_command_store.get(
        UUID(confirmed.json()["command"]["id"])
    )
    assert persisted.attempts[-1].credential_id == replacement.id
    assert setup["adapter"].post_calls == 1


def test_reconcile_rejects_changed_provider_account_binding_before_meta_read(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    setup["adapter"].apply_write = False
    preview = _preview(setup)
    confirmed = client.post(
        "/provider-controls/meta/budget-changes",
        json={**_change_payload(setup), "preview_token": preview["preview_token"], "confirm": True},
        headers={**_headers(setup["owner_token"]), "Idempotency-Key": "budget-rebind-account-1"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["command"]["status"] == "unknown"
    reads_before = setup["adapter"].read_calls

    app.state.ad_account_store.patch(
        UUID(setup["account"]["id"]),
        main_module.AdAccountPatch(external_account_id="act_654321"),
    )
    app.state.integration_credential_store.bind_provider_account(
        ad_account_id=UUID(setup["account"]["id"]),
        provider="meta",
        external_account_id="654321",
        credential_id=setup["credential"].id,
        bound_by=UUID(setup["owner"]["id"]),
    )
    response = client.post(
        f"/provider-controls/meta/budget-changes/{confirmed.json()['command']['id']}/reconcile",
        headers=_headers(setup["owner_token"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "meta_budget_recovery_account_binding_mismatch"
    assert setup["adapter"].read_calls == reads_before
    assert setup["adapter"].post_calls == 1


def test_agency_budget_api_role_matrix_and_tenant_isolation(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    agency_setup = _provision_agency_roles(setup)
    agency_id = agency_setup["agency"]["id"]
    owner = agency_setup["users"]["owner"]
    manager = agency_setup["users"]["manager"]
    member = agency_setup["users"]["member"]

    owner_payload = {**_change_payload(setup, amount_minor=2200), "agency_id": agency_id}
    owner_preview = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=owner_payload,
        headers=_headers(owner["token"]),
    )
    assert owner_preview.status_code == 200, owner_preview.text
    manager_preview = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json={**_change_payload(setup, amount_minor=2300), "agency_id": agency_id},
        headers=_headers(manager["token"]),
    )
    assert manager_preview.status_code == 200, manager_preview.text

    confirmed = client.post(
        "/provider-controls/meta/budget-changes",
        json={
            **owner_payload,
            "preview_token": owner_preview.json()["preview_token"],
            "confirm": True,
        },
        headers={**_headers(owner["token"]), "Idempotency-Key": "agency-role-api-1"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["command"]["status"] == "applied"

    member_preview = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json={**_change_payload(setup, amount_minor=2400, expected_current_minor=2200), "agency_id": agency_id},
        headers=_headers(member["token"]),
    )
    assert member_preview.status_code == 403, member_preview.text
    assert member_preview.json()["error"]["code"] == "agency_manage_forbidden"
    member_readiness = client.get(
        "/provider-controls/meta/readiness",
        params={
            "client_id": setup["tenant"]["id"],
            "ad_account_id": setup["account"]["id"],
            "agency_id": agency_id,
        },
        headers=_headers(member["token"]),
    )
    assert member_readiness.status_code == 200, member_readiness.text
    assert member_readiness.json()["can_reconcile"] is False
    member_confirm = client.post(
        "/provider-controls/meta/budget-changes",
        json={
            **_change_payload(setup, amount_minor=2300),
            "agency_id": agency_id,
            "preview_token": manager_preview.json()["preview_token"],
            "confirm": True,
        },
        headers={**_headers(member["token"]), "Idempotency-Key": "agency-role-api-member"},
    )
    assert member_confirm.status_code == 403, member_confirm.text
    assert member_confirm.json()["error"]["code"] == "agency_manage_forbidden"

    history = client.get(
        "/provider-controls/meta/budget-changes",
        params={"client_id": setup["tenant"]["id"], "agency_id": agency_id},
        headers=_headers(member["token"]),
    )
    assert history.status_code == 200, history.text
    assert history.json()["count"] == 1
    detail = client.get(
        f"/provider-controls/meta/budget-changes/{confirmed.json()['command']['id']}",
        headers=_headers(member["token"]),
    )
    assert detail.status_code == 200, detail.text


    admin_denied = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=_change_payload(setup, amount_minor=2400, expected_current_minor=2200),
        headers=_headers(setup["admin_token"]),
    )
    assert admin_denied.status_code == 403, admin_denied.text
    assert admin_denied.json()["error"]["code"] == "admin_write_not_delegated"
    admin_readiness = client.get(
        "/provider-controls/meta/readiness",
        params={"client_id": setup["tenant"]["id"], "ad_account_id": setup["account"]["id"]},
        headers=_headers(setup["admin_token"]),
    )
    assert admin_readiness.status_code == 200, admin_readiness.text
    assert admin_readiness.json()["can_reconcile"] is False

    foreign_agency_response = client.post(
        "/platform/agencies",
        json={"name": "Foreign agency", "slug": f"foreign-{uuid4().hex[:8]}"},
        headers=_headers(setup["admin_token"]),
    )
    assert foreign_agency_response.status_code == 200
    foreign_agency = foreign_agency_response.json()
    assert client.post(
        f"/platform/agencies/{foreign_agency['id']}/members",
        json={"user_id": manager["id"], "role": "manager", "status": "active"},
        headers=_headers(setup["admin_token"]),
    ).status_code == 200
    wrong_agency = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json={
            **_change_payload(setup, amount_minor=2400, expected_current_minor=2200),
            "agency_id": foreign_agency["id"],
        },
        headers=_headers(manager["token"]),
    )
    assert wrong_agency.status_code == 403, wrong_agency.text
    assert wrong_agency.json()["error"]["code"] == "agency_client_access_denied"

    foreign_client_response = client.post(
        "/clients",
        json={"name": "Foreign budget tenant", "status": "active", "default_currency": "USD"},
        headers=_headers(setup["admin_token"]),
    )
    assert foreign_client_response.status_code == 200
    foreign_client = foreign_client_response.json()
    foreign_account_response = client.post(
        "/ad-accounts",
        json={
            "client_id": foreign_client["id"],
            "platform": "meta",
            "external_account_id": "act_999999",
            "name": "Foreign Meta account",
            "currency": "USD",
            "status": "active",
        },
        headers=_headers(setup["admin_token"]),
    )
    assert foreign_account_response.status_code == 200
    foreign_account = foreign_account_response.json()
    monkeypatch.setenv(
        "META_BUDGET_ALLOWED_CLIENT_IDS",
        f"{setup['tenant']['id']},{foreign_client['id']}",
    )
    wrong_client = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json={**_change_payload(setup, amount_minor=2400, expected_current_minor=2200), "client_id": foreign_client["id"], "agency_id": agency_id},
        headers=_headers(manager["token"]),
    )
    assert wrong_client.status_code == 409, wrong_client.text
    assert wrong_client.json()["error"]["code"] == "meta_budget_account_scope_mismatch"
    wrong_account = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json={
            **_change_payload(setup, amount_minor=2400, expected_current_minor=2200),
            "client_id": foreign_client["id"],
            "ad_account_id": foreign_account["id"],
            "agency_id": agency_id,
        },
        headers=_headers(manager["token"]),
    )
    assert wrong_account.status_code == 403, wrong_account.text
    assert setup["adapter"].post_calls == 1


def test_agency_role_change_between_preview_and_confirm_invalidates_preview(monkeypatch):
    setup = _reset_and_provision(monkeypatch)
    agency_setup = _provision_agency_roles(setup)
    agency_id = agency_setup["agency"]["id"]
    owner = agency_setup["users"]["owner"]
    payload = {**_change_payload(setup), "agency_id": agency_id}
    preview = client.post(
        "/provider-controls/meta/budget-changes/preview",
        json=payload,
        headers=_headers(owner["token"]),
    )
    assert preview.status_code == 200, preview.text

    successor = agency_setup["users"]["manager"]
    promoted = client.post(
        f"/platform/agencies/{agency_id}/members",
        json={"user_id": successor["id"], "role": "owner", "status": "active"},
        headers=_headers(setup["admin_token"]),
    )
    assert promoted.status_code == 200, promoted.text
    changed = client.post(
        f"/platform/agencies/{agency_id}/members",
        json={"user_id": owner["id"], "role": "manager", "status": "active"},
        headers=_headers(setup["admin_token"]),
    )
    assert changed.status_code == 200, changed.text
    response = client.post(
        "/provider-controls/meta/budget-changes",
        json={**payload, "preview_token": preview.json()["preview_token"], "confirm": True},
        headers={**_headers(owner["token"]), "Idempotency-Key": "agency-role-changed-1"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "preview_authorization_changed"
    assert setup["adapter"].post_calls == 0
