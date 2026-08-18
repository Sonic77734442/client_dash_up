from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.provider_budget_commands import (
    ActorRole,
    AgencyMemberRole,
    BudgetActorContext,
    BudgetChangeRequest,
    BudgetCommandStatus,
    BudgetConflictError,
    BudgetCredentialContext,
    BudgetExecutionAuthorization,
    BudgetField,
    BudgetPolicyError,
    BudgetSnapshot,
    BudgetTargetType,
    BudgetTenantContext,
    BudgetValidationError,
    CommandNotExecutableError,
    CredentialScope,
    IdempotencyConflictError,
    InMemoryBudgetCommandStore,
    PreviewExpiredError,
    PreviewSigner,
    PreviewTokenError,
    ProviderBudgetCommandService,
    ProviderBudgetError,
    ProviderWriteOutcomeUnknown,
    ProviderWriteReceipt,
    authorize_budget_reconciliation,
    authorize_budget_write,
    budget_authorization_snapshot,
    budget_credential_audit_snapshot,
)


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def make_request(**overrides) -> BudgetChangeRequest:
    payload = {
        "client_id": uuid4(),
        "ad_account_id": uuid4(),
        "provider_account_id": "123456",
        "credential_id": uuid4(),
        "agency_id": uuid4(),
        "target_type": BudgetTargetType.CAMPAIGN,
        "provider_target_id": "777",
        "field": BudgetField.DAILY_BUDGET,
        "amount_minor": 2500,
        "expected_current_minor": 1000,
        "currency": "USD",
        "reason": "Approved pacing correction",
    }
    payload.update(overrides)
    return BudgetChangeRequest(**payload)


def make_contexts(request: BudgetChangeRequest):
    actor = BudgetActorContext(
        user_id=uuid4(),
        role=ActorRole.AGENCY,
        accessible_client_ids=frozenset({request.client_id}),
        selected_agency_id=request.agency_id,
        agency_active=True,
        agency_member_role=AgencyMemberRole.OWNER,
        agency_bound_client_ids=frozenset({request.client_id}),
        can_manage_spend_cap=True,
    )
    tenant = BudgetTenantContext(
        client_id=request.client_id,
        ad_account_id=request.ad_account_id,
        account_client_id=request.client_id,
        client_active=True,
        account_active=True,
        account_platform="meta",
    )
    credential = BudgetCredentialContext(
        id=request.credential_id,
        provider="meta",
        status="active",
        scope_type=CredentialScope.AGENCY,
        scope_id=request.agency_id,
        created_by=actor.user_id,
        has_ads_management=True,
        revision=1,
        meta_app_id="meta-app",
        meta_business_config_id="business-config",
        meta_user_id="meta-user",
        granted_permissions=("ads_management",),
        validation_version=1,
        validated_at=NOW,
    )
    return actor, tenant, credential


class FakeBudgetAdapter:
    def __init__(self, request: BudgetChangeRequest, *, current_minor: int = 1000):
        self.request = request
        self.current_minor = current_minor
        self.read_calls = 0
        self.write_calls = 0
        self.apply_write = True
        self.apply_before_write_error = False
        self.write_error = None

    def read_budget(self, request: BudgetChangeRequest) -> BudgetSnapshot:
        self.read_calls += 1
        return BudgetSnapshot(
            target_type=request.target_type,
            provider_target_id=request.provider_target_id,
            provider_account_id=request.provider_account_id,
            field=request.field,
            amount_minor=self.current_minor,
            currency=request.currency,
            observed_at=NOW,
            provider_trace_id=f"read-{self.read_calls}",
        )

    def write_budget(self, request: BudgetChangeRequest) -> ProviderWriteReceipt:
        self.write_calls += 1
        if self.apply_before_write_error:
            self.current_minor = request.amount_minor
        if self.write_error is not None:
            raise self.write_error
        if self.apply_write:
            self.current_minor = request.amount_minor
        return ProviderWriteReceipt(accepted=True, provider_trace_id="write-trace")


def make_service(request: BudgetChangeRequest, adapter: FakeBudgetAdapter):
    return ProviderBudgetCommandService(
        adapter=adapter,
        store=InMemoryBudgetCommandStore(),
        preview_signer=PreviewSigner(b"preview-signing-secret-32-bytes!!", ttl_seconds=60),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_minor": 1.25},
        {"amount_minor": True},
        {"amount_minor": 0},
        {"currency": "usd"},
        {"provider_target_id": "act_777"},
        {"target_type": "campaign", "field": "spend_cap"},
        {
            "target_type": "account",
            "field": "daily_budget",
            "provider_target_id": "123456",
        },
    ],
)
def test_change_request_rejects_ambiguous_or_unsafe_values(overrides):
    with pytest.raises(BudgetValidationError):
        make_request(**overrides)


def test_account_spend_cap_allows_zero_but_requires_account_target_identity():
    request = make_request(
        target_type="account",
        field="spend_cap",
        provider_target_id="123456",
        amount_minor=0,
    )
    assert request.amount_minor == 0

    with pytest.raises(BudgetValidationError) as exc_info:
        make_request(
            target_type="account",
            field="spend_cap",
            provider_target_id="999999",
        )
    assert exc_info.value.code == "account_target_mismatch"


def test_preview_token_is_bound_to_actor_request_and_ttl():
    request = make_request()
    actor, _, credential = make_contexts(request)
    signer = PreviewSigner(b"preview-signing-secret-32-bytes!!", ttl_seconds=60)
    authorization_snapshot = budget_authorization_snapshot(actor, request)
    credential_snapshot = budget_credential_audit_snapshot(credential)
    preview = signer.issue(
        request,
        actor_user_id=actor.user_id,
        authorization_snapshot=authorization_snapshot,
        credential_snapshot=credential_snapshot,
        observed_before_minor=1000,
        now=NOW,
    )
    encoded_body = preview.token.split(".", 1)[0]
    token_payload = json.loads(
        base64.urlsafe_b64decode(encoded_body + "=" * (-len(encoded_body) % 4)).decode("utf-8")
    )
    assert "actor_user_id" not in token_payload
    assert "credential_id" not in token_payload
    assert "credential_revision" not in token_payload
    assert "meta_user_id" not in token_payload

    verified = signer.verify(
        preview.token,
        request,
        actor_user_id=actor.user_id,
        authorization_snapshot=authorization_snapshot,
        credential_snapshot=credential_snapshot,
        now=NOW + timedelta(seconds=59),
    )
    assert verified.observed_before_minor == 1000

    tampered = f"{preview.token[:-1]}{'A' if preview.token[-1] != 'A' else 'B'}"
    with pytest.raises(PreviewTokenError):
        signer.verify(tampered, request, actor_user_id=actor.user_id, authorization_snapshot=authorization_snapshot, credential_snapshot=credential_snapshot, now=NOW)
    # Base64url segments are unpadded and canonical. Permissive alternate
    # spellings must not produce a second token identity for the same bytes.
    with pytest.raises(PreviewTokenError) as noncanonical:
        signer.verify(f"{preview.token}=", request, actor_user_id=actor.user_id, authorization_snapshot=authorization_snapshot, credential_snapshot=credential_snapshot, now=NOW)
    assert noncanonical.value.code == "preview_invalid"
    with pytest.raises(PreviewTokenError):
        signer.verify(preview.token, replace(request, amount_minor=2600), actor_user_id=actor.user_id, authorization_snapshot=authorization_snapshot, credential_snapshot=credential_snapshot, now=NOW)
    with pytest.raises(PreviewTokenError):
        signer.verify(preview.token, request, actor_user_id=uuid4(), authorization_snapshot=authorization_snapshot, credential_snapshot=credential_snapshot, now=NOW)
    with pytest.raises(PreviewExpiredError):
        signer.verify(preview.token, request, actor_user_id=actor.user_id, authorization_snapshot=authorization_snapshot, credential_snapshot=credential_snapshot, now=NOW + timedelta(seconds=60))


def test_reconnect_same_credential_id_and_role_change_invalidate_preview_without_write():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    service = make_service(request, adapter)

    reconnect_preview = service.preview(
        request,
        actor=actor,
        tenant=tenant,
        credential=credential,
    )
    reconnected = replace(credential, revision=credential.revision + 1)
    with pytest.raises(PreviewTokenError) as reconnect_error:
        service.submit(
            request,
            preview_token=reconnect_preview.token,
            idempotency_key="credential-reconnect-preview-1",
            actor=actor,
            tenant=tenant,
            credential=reconnected,
        )
    assert reconnect_error.value.code == "preview_credential_changed"

    role_preview = service.preview(
        request,
        actor=actor,
        tenant=tenant,
        credential=credential,
    )
    changed_role = replace(actor, agency_member_role=AgencyMemberRole.MANAGER)
    with pytest.raises(PreviewTokenError) as role_error:
        service.submit(
            request,
            preview_token=role_preview.token,
            idempotency_key="authorization-change-preview-1",
            actor=changed_role,
            tenant=tenant,
            credential=credential,
        )
    assert role_error.value.code == "preview_authorization_changed"
    assert adapter.write_calls == 0


def test_pre_write_context_revalidation_rejects_change_after_command_is_queued():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    service = make_service(request, adapter)
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submission = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="authorization-after-submit-1",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    with pytest.raises(BudgetConflictError) as error:
        service.execute(
            submission.command.id,
            actor=replace(actor, agency_member_role=AgencyMemberRole.MANAGER),
            tenant=tenant,
            credential=credential,
        )
    assert error.value.code == "provider_budget_authorization_changed"
    assert adapter.write_calls == 0


def test_final_live_reauthorization_runs_after_provider_read_and_audits_its_snapshot():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    store = InMemoryBudgetCommandStore()
    signer = PreviewSigner(b"preview-signing-secret-32-bytes!!", ttl_seconds=60)

    def live_gate(command):
        assert command.request == request
        # Preview and the execution preflight read have completed, but the
        # provider mutation has not started.
        assert adapter.read_calls == 2
        assert adapter.write_calls == 0
        return BudgetExecutionAuthorization(
            actor=replace(actor, agency_member_role=AgencyMemberRole.MEMBER),
            tenant=tenant,
            credential=credential,
            provider_account_id=request.provider_account_id,
            feature_enabled=True,
            client_rollout_allowed=True,
            binding_verified=True,
            spend_cap_enabled=True,
        )

    service = ProviderBudgetCommandService(
        adapter=adapter,
        store=store,
        preview_signer=signer,
        clock=lambda: NOW,
        reauthorize_before_write=live_gate,
    )
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submission = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="live-reauthorization-after-read-1",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    command = service.execute(
        submission.command.id,
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    assert command.status == BudgetCommandStatus.FAILED
    assert command.error is not None
    assert command.error.code == "agency_manage_forbidden"
    assert adapter.write_calls == 0
    attempt = command.attempts[-1]
    assert attempt.authorization_snapshot is not None
    assert attempt.authorization_snapshot.agency_member_role == AgencyMemberRole.MEMBER
    assert attempt.authorization_snapshot.feature_enabled is True
    assert attempt.authorization_snapshot.client_rollout_allowed is True
    assert attempt.authorization_snapshot.binding_verified is True
    assert attempt.authorization_snapshot.provider_account_id == request.provider_account_id
    assert attempt.authorization_snapshot.spend_cap_enabled is True
    assert attempt.authorization_snapshot.final_gate_verified is False
    assert attempt.authorization_snapshot.final_gate_failure_code == "agency_manage_forbidden"
    assert attempt.credential_snapshot is not None
    assert attempt.credential_snapshot.final_gate_verified is False
    assert attempt.credential_snapshot.final_gate_failure_code == "agency_manage_forbidden"


def test_failed_live_callback_records_unknown_not_preview_authorization_or_credential():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    store = InMemoryBudgetCommandStore()
    signer = PreviewSigner(b"preview-signing-secret-32-bytes!!", ttl_seconds=60)

    def live_gate(_command):
        assert adapter.read_calls == 2
        raise BudgetPolicyError(
            "meta_budget_rediscovery_required",
            "The trusted binding was revoked",
        )

    service = ProviderBudgetCommandService(
        adapter=adapter,
        store=store,
        preview_signer=signer,
        clock=lambda: NOW,
        reauthorize_before_write=live_gate,
    )
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submission = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="live-reauthorization-unknown-audit-1",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    command = service.execute(
        submission.command.id,
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    assert command.status == BudgetCommandStatus.FAILED
    assert adapter.write_calls == 0
    attempt = command.attempts[-1]
    authorization = attempt.authorization_snapshot
    assert authorization is not None
    assert authorization.actor_role is None
    assert authorization.selected_agency_id is None
    assert authorization.agency_member_role is None
    assert authorization.agency_active is False
    assert authorization.can_manage_spend_cap is False
    assert authorization.has_client_access is False
    assert authorization.agency_client_bound is False
    assert authorization.binding_verified is None
    assert authorization.spend_cap_enabled is None
    assert authorization.final_gate_verified is False
    assert authorization.final_gate_failure_code == "meta_budget_rediscovery_required"
    assert attempt.credential_id is None
    credential_audit = attempt.credential_snapshot
    assert credential_audit is not None
    assert credential_audit.revision == 0
    assert credential_audit.meta_app_id == ""
    assert credential_audit.meta_business_config_id == ""
    assert credential_audit.meta_user_id == ""
    assert credential_audit.granted_permissions == ()
    assert credential_audit.validated_at is None
    assert credential_audit.final_gate_verified is False
    assert credential_audit.final_gate_failure_code == "meta_budget_rediscovery_required"


@pytest.mark.parametrize("spend_cap_enabled", [False, True])
def test_final_live_spend_cap_kill_switch_is_required_and_audited(spend_cap_enabled):
    request = make_request(
        target_type=BudgetTargetType.ACCOUNT,
        provider_target_id="123456",
        field=BudgetField.SPEND_CAP,
    )
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    store = InMemoryBudgetCommandStore()
    signer = PreviewSigner(b"preview-signing-secret-32-bytes!!", ttl_seconds=60)

    def live_gate(_command):
        assert adapter.read_calls == 2
        assert adapter.write_calls == 0
        return BudgetExecutionAuthorization(
            actor=replace(actor, can_manage_spend_cap=spend_cap_enabled),
            tenant=tenant,
            credential=credential,
            provider_account_id=request.provider_account_id,
            feature_enabled=True,
            client_rollout_allowed=True,
            binding_verified=True,
            spend_cap_enabled=spend_cap_enabled,
        )

    service = ProviderBudgetCommandService(
        adapter=adapter,
        store=store,
        preview_signer=signer,
        clock=lambda: NOW,
        reauthorize_before_write=live_gate,
    )
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submission = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key=f"live-spend-cap-{spend_cap_enabled}",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )
    command = service.execute(
        submission.command.id,
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    assert adapter.write_calls == (1 if spend_cap_enabled else 0)
    assert command.status == (
        BudgetCommandStatus.APPLIED if spend_cap_enabled else BudgetCommandStatus.FAILED
    )
    if not spend_cap_enabled:
        assert command.error is not None
        assert command.error.code == "meta_spend_cap_disabled"
    attempt = command.attempts[-1]
    assert attempt.authorization_snapshot is not None
    assert attempt.authorization_snapshot.spend_cap_enabled is spend_cap_enabled
    assert attempt.authorization_snapshot.final_gate_verified is spend_cap_enabled
    assert attempt.credential_snapshot is not None
    assert attempt.credential_snapshot.final_gate_verified is spend_cap_enabled


def test_policy_denies_client_and_cross_tenant_access():
    request = make_request()
    actor, tenant, credential = make_contexts(request)

    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(request, actor=replace(actor, role=ActorRole.CLIENT), tenant=tenant, credential=credential)
    assert exc_info.value.code == "client_read_only"

    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(
            request,
            actor=replace(actor, accessible_client_ids=frozenset()),
            tenant=tenant,
            credential=credential,
        )
    assert exc_info.value.code == "tenant_access_denied"


def test_agency_requires_manager_and_its_own_explicit_credential():
    request = make_request()
    actor, tenant, credential = make_contexts(request)

    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(
            request,
            actor=replace(actor, agency_member_role=AgencyMemberRole.MEMBER),
            tenant=tenant,
            credential=credential,
        )
    assert exc_info.value.code == "agency_manage_forbidden"

    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(
            request,
            actor=actor,
            tenant=tenant,
            credential=replace(credential, scope_type=CredentialScope.CLIENT, scope_id=request.client_id),
        )
    assert exc_info.value.code == "agency_credential_mismatch"


def test_account_spend_cap_role_capability_matrix():
    request = make_request(
        target_type=BudgetTargetType.ACCOUNT,
        provider_target_id="123456",
        field=BudgetField.SPEND_CAP,
    )
    actor, tenant, credential = make_contexts(request)
    owner_without_capability = replace(actor, can_manage_spend_cap=False)
    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(
            request,
            actor=owner_without_capability,
            tenant=tenant,
            credential=credential,
        )
    assert exc_info.value.code == "spend_cap_forbidden"

    authorize_budget_write(request, actor=actor, tenant=tenant, credential=credential)

    manager = replace(actor, agency_member_role=AgencyMemberRole.MANAGER, can_manage_spend_cap=False)

    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(request, actor=manager, tenant=tenant, credential=credential)
    assert exc_info.value.code == "spend_cap_forbidden"

    authorize_budget_write(
        request,
        actor=replace(manager, can_manage_spend_cap=True),
        tenant=tenant,
        credential=credential,
    )

    solo = BudgetActorContext(
        user_id=actor.user_id,
        role=ActorRole.SOLO_CLIENT,
        accessible_client_ids=frozenset({request.client_id}),
        solo_owner_client_id=request.client_id,
        can_manage_spend_cap=True,
    )
    solo_credential = replace(
        credential,
        scope_type=CredentialScope.CLIENT,
        scope_id=request.client_id,
        created_by=solo.user_id,
    )
    solo_request = replace(request, agency_id=None)
    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(
            solo_request,
            actor=solo,
            tenant=tenant,
            credential=solo_credential,
        )
    assert exc_info.value.code == "spend_cap_forbidden"

    admin = BudgetActorContext(
        user_id=actor.user_id,
        role=ActorRole.ADMIN,
        can_admin_provider_write=True,
        can_manage_spend_cap=False,
    )
    global_credential = replace(
        credential,
        scope_type=CredentialScope.GLOBAL,
        scope_id=None,
    )
    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(
            request,
            actor=admin,
            tenant=tenant,
            credential=global_credential,
        )
    assert exc_info.value.code == "spend_cap_forbidden"
    authorize_budget_write(
        request,
        actor=replace(admin, can_manage_spend_cap=True),
        tenant=tenant,
        credential=global_credential,
    )

    # Disabling the mutation capability must not strand an UNKNOWN command:
    # agency owner/manager recovery is provider-read-only.
    authorize_budget_reconciliation(
        request,
        actor=owner_without_capability,
        tenant=tenant,
        credential=credential,
    )
    authorize_budget_reconciliation(
        request,
        actor=manager,
        tenant=tenant,
        credential=credential,
    )


def test_solo_owner_must_use_own_client_credential():
    request = make_request(agency_id=None)
    actor, tenant, credential = make_contexts(request)
    solo = BudgetActorContext(
        user_id=actor.user_id,
        role=ActorRole.SOLO_CLIENT,
        accessible_client_ids=frozenset({request.client_id}),
        solo_owner_client_id=request.client_id,
    )
    own_credential = replace(
        credential,
        scope_type=CredentialScope.CLIENT,
        scope_id=request.client_id,
        created_by=solo.user_id,
    )
    authorize_budget_write(request, actor=solo, tenant=tenant, credential=own_credential)

    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(
            request,
            actor=solo,
            tenant=tenant,
            credential=replace(own_credential, created_by=uuid4()),
        )
    assert exc_info.value.code == "solo_credential_mismatch"


def test_admin_requires_explicit_write_capability_and_global_credential():
    request = make_request(agency_id=None)
    agency_actor, tenant, agency_credential = make_contexts(request)
    admin = BudgetActorContext(user_id=agency_actor.user_id, role=ActorRole.ADMIN)
    global_credential = replace(
        agency_credential,
        scope_type=CredentialScope.GLOBAL,
        scope_id=None,
    )

    with pytest.raises(BudgetPolicyError) as exc_info:
        authorize_budget_write(request, actor=admin, tenant=tenant, credential=global_credential)
    assert exc_info.value.code == "admin_write_not_delegated"

    authorize_budget_write(
        request,
        actor=replace(admin, can_admin_provider_write=True),
        tenant=tenant,
        credential=global_credential,
    )


def test_preview_and_submit_are_read_only_and_idempotent():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    service = make_service(request, adapter)

    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    first = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0001",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )
    replay = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0001",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    assert adapter.read_calls == 1
    assert adapter.write_calls == 0
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.command.id == first.command.id


def test_same_idempotency_key_with_different_request_conflicts():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    service = make_service(request, adapter)
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0002",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    changed = replace(request, amount_minor=3000)
    changed_preview = service.preview(changed, actor=actor, tenant=tenant, credential=credential)
    with pytest.raises(IdempotencyConflictError):
        service.submit(
            changed,
            preview_token=changed_preview.token,
            idempotency_key="budget-command-0002",
            actor=actor,
            tenant=tenant,
            credential=credential,
        )


def test_execute_performs_exactly_read_write_read_and_confirms_value():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    service = make_service(request, adapter)
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submitted = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0003",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    command = service.execute(submitted.command.id, actor=actor, tenant=tenant, credential=credential)

    assert command.status == BudgetCommandStatus.APPLIED
    assert command.confirmed_after_minor == request.amount_minor
    assert adapter.read_calls == 3  # preview, execution preflight, read-after-write
    assert adapter.write_calls == 1
    assert command.attempts[0].observed_before_minor == 1000
    assert command.attempts[0].confirmed_after_minor == request.amount_minor


def test_stale_preview_conflicts_without_provider_write():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    service = make_service(request, adapter)
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submitted = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0004",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )
    adapter.current_minor = 1100

    command = service.execute(submitted.command.id, actor=actor, tenant=tenant, credential=credential)

    assert command.status == BudgetCommandStatus.CONFLICT
    assert adapter.write_calls == 0


def test_ambiguous_write_becomes_unknown_and_is_never_blindly_retried():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    adapter.apply_before_write_error = True
    adapter.write_error = ProviderWriteOutcomeUnknown(
        "provider_timeout",
        "Timeout after request was sent",
    )
    service = make_service(request, adapter)
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submitted = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0005",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    command = service.execute(submitted.command.id, actor=actor, tenant=tenant, credential=credential)
    assert command.status == BudgetCommandStatus.UNKNOWN
    assert adapter.write_calls == 1

    with pytest.raises(CommandNotExecutableError):
        service.execute(submitted.command.id, actor=actor, tenant=tenant, credential=credential)
    assert adapter.write_calls == 1

    reconciled = service.reconcile(
        submitted.command.id,
        actor=actor,
        tenant=tenant,
        credential=credential,
    )
    assert reconciled.status == BudgetCommandStatus.APPLIED
    assert reconciled.confirmed_after_minor == request.amount_minor
    assert adapter.write_calls == 1
    assert reconciled.attempts[-1].reconciliation is True


def test_current_agency_manager_can_reconcile_predecessor_unknown_with_durable_actor():
    request = make_request()
    original, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    adapter.apply_write = False
    service = make_service(request, adapter)
    preview = service.preview(request, actor=original, tenant=tenant, credential=credential)
    submitted = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-successor-0001",
        actor=original,
        tenant=tenant,
        credential=credential,
    )
    unknown = service.execute(
        submitted.command.id,
        actor=original,
        tenant=tenant,
        credential=credential,
    )
    assert unknown.status == BudgetCommandStatus.UNKNOWN

    successor = replace(
        original,
        user_id=uuid4(),
        agency_member_role=AgencyMemberRole.MANAGER,
    )
    adapter.current_minor = request.amount_minor
    reconciled = service.reconcile(
        submitted.command.id,
        actor=successor,
        tenant=tenant,
        credential=credential,
    )
    assert reconciled.status == BudgetCommandStatus.APPLIED
    assert reconciled.attempts[-1].actor_user_id == successor.user_id
    assert reconciled.attempts[-1].credential_id == credential.id
    assert adapter.write_calls == 1


def test_delegated_admin_can_read_reconcile_agency_unknown_without_provider_write():
    request = make_request()
    original, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    adapter.apply_write = False
    service = make_service(request, adapter)
    preview = service.preview(request, actor=original, tenant=tenant, credential=credential)
    submitted = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-admin-recovery-1",
        actor=original,
        tenant=tenant,
        credential=credential,
    )
    unknown = service.execute(
        submitted.command.id,
        actor=original,
        tenant=tenant,
        credential=credential,
    )
    assert unknown.status == BudgetCommandStatus.UNKNOWN
    adapter.current_minor = request.amount_minor
    admin = BudgetActorContext(
        user_id=uuid4(),
        role=ActorRole.ADMIN,
        can_admin_provider_write=True,
    )
    reconciled = service.reconcile(
        submitted.command.id,
        actor=admin,
        tenant=tenant,
        credential=credential,
    )
    assert reconciled.status == BudgetCommandStatus.APPLIED
    assert reconciled.attempts[-1].actor_user_id == admin.user_id
    assert adapter.write_calls == 1


def test_readback_mismatch_remains_unknown_until_explicit_reconciliation_policy():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    adapter.apply_write = False
    service = make_service(request, adapter)
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submitted = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0006",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    command = service.execute(submitted.command.id, actor=actor, tenant=tenant, credential=credential)
    assert command.status == BudgetCommandStatus.UNKNOWN

    pending = service.reconcile(
        submitted.command.id,
        actor=actor,
        tenant=tenant,
        credential=credential,
    )
    assert pending.status == BudgetCommandStatus.UNKNOWN
    assert adapter.write_calls == 1

    final = service.reconcile(
        submitted.command.id,
        actor=actor,
        tenant=tenant,
        credential=credential,
        finalize_mismatch=True,
    )
    assert final.status == BudgetCommandStatus.CONFLICT
    assert adapter.write_calls == 1


def test_definitive_provider_error_is_sanitized_and_not_retried():
    request = make_request()
    actor, tenant, credential = make_contexts(request)
    adapter = FakeBudgetAdapter(request)
    adapter.write_error = ProviderBudgetError(
        "190",
        "Invalid token access_token=super-secret-token Bearer another-secret",
    )
    service = make_service(request, adapter)
    preview = service.preview(request, actor=actor, tenant=tenant, credential=credential)
    submitted = service.submit(
        request,
        preview_token=preview.token,
        idempotency_key="budget-command-0007",
        actor=actor,
        tenant=tenant,
        credential=credential,
    )

    command = service.execute(submitted.command.id, actor=actor, tenant=tenant, credential=credential)

    assert command.status == BudgetCommandStatus.FAILED
    assert command.error is not None
    assert "super-secret-token" not in command.error.message
    assert "another-secret" not in command.error.message
    assert "[REDACTED]" in command.error.message
    assert adapter.write_calls == 1
