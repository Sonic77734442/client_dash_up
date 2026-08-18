from __future__ import annotations

from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest

from app.services.provider_budget_commands import (
    BudgetChangeRequest,
    BudgetConflictError,
    BudgetField,
    BudgetTargetType,
    ProviderBudgetError,
    ProviderWriteOutcomeUnknown,
)
from app.services.providers.meta_budget import MetaBudgetAdapter


TOKEN = "tenant-meta-token-super-secret"


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


def make_adapter(handler) -> tuple[MetaBudgetAdapter, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return (
        MetaBudgetAdapter(
            access_token=TOKEN,
            graph_version="v25.0",
            client=client,
        ),
        client,
    )


def test_budget_target_list_returns_only_effective_campaign_and_ad_set_budget_owners():
    calls = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append((http_request.method, http_request.url.path, dict(http_request.url.params)))
        assert http_request.method == "GET"
        assert http_request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert "access_token" not in str(http_request.url)
        if http_request.url.path == "/v25.0/act_123456" and "campaigns" not in http_request.url.path:
            return httpx.Response(
                200,
                json={"id": "act_123456", "account_id": "123456", "currency": "USD"},
            )
        if http_request.url.path == "/v25.0/act_123456/campaigns":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "700",
                            "name": "CBO campaign",
                            "effective_status": "ACTIVE",
                            "account_id": "123456",
                            "daily_budget": "5000",
                            "lifetime_budget": "0",
                        },
                        {
                            "id": "701",
                            "name": "ABO campaign",
                            "effective_status": "ACTIVE",
                            "account_id": "123456",
                            "daily_budget": "0",
                            "lifetime_budget": "0",
                        },
                    ]
                },
            )
        assert http_request.url.path == "/v25.0/act_123456/adsets"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "800",
                        "campaign_id": "700",
                        "name": "CBO child (not an owner)",
                        "effective_status": "ACTIVE",
                        "account_id": "123456",
                        "daily_budget": "1000",
                        "lifetime_budget": "0",
                    },
                    {
                        "id": "801",
                        "campaign_id": "701",
                        "name": "ABO owner",
                        "effective_status": "PAUSED",
                        "account_id": "123456",
                        "daily_budget": "0",
                        "lifetime_budget": "12000",
                    },
                ]
            },
        )

    adapter, client = make_adapter(handler)
    try:
        targets = adapter.list_budget_targets("123456")
    finally:
        client.close()

    assert [(target.target_type.value, target.provider_target_id) for target in targets] == [
        ("ad_set", "801"),
        ("campaign", "700"),
    ]
    assert targets[0].budget_fields[0].field == BudgetField.LIFETIME_BUDGET
    assert targets[0].budget_fields[0].current_minor == 12000
    assert targets[1].budget_fields[0].field == BudgetField.DAILY_BUDGET
    assert len(calls) == 3


def test_budget_target_pagination_rebuilds_fixed_graph_url_from_validated_cursor():
    page = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal page
        if http_request.url.path == "/v25.0/act_123456":
            return httpx.Response(
                200,
                json={"id": "act_123456", "account_id": "123456", "currency": "USD"},
            )
        if http_request.url.path.endswith("/campaigns"):
            page += 1
            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [],
                        "paging": {
                            "next": (
                                "https://graph.facebook.com/v25.0/act_123456/campaigns"
                                "?after=opaque_CURSOR-1&access_token=must-not-be-forwarded"
                            )
                        },
                    },
                )
            assert http_request.url.params["after"] == "opaque_CURSOR-1"
            assert "access_token" not in http_request.url.params
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": []})

    adapter, client = make_adapter(handler)
    try:
        assert adapter.list_budget_targets("123456") == []
    finally:
        client.close()
    assert page == 2


def test_budget_target_pagination_rejects_non_graph_next_host():
    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path == "/v25.0/act_123456":
            return httpx.Response(
                200,
                json={"id": "act_123456", "account_id": "123456", "currency": "USD"},
            )
        return httpx.Response(
            200,
            json={"data": [], "paging": {"next": "https://evil.example/v25.0/act_123456/campaigns?after=x"}},
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.list_budget_targets("123456")
    finally:
        client.close()
    assert exc_info.value.code == "meta_pagination_invalid"


def test_account_spend_cap_read_and_write_use_allowlisted_object_and_field():
    request = make_request(
        target_type=BudgetTargetType.ACCOUNT,
        provider_target_id="123456",
        field=BudgetField.SPEND_CAP,
        amount_minor=500_000,
        expected_current_minor=400_000,
    )
    calls = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request)
        assert http_request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert "access_token" not in str(http_request.url)
        if http_request.method == "GET":
            assert http_request.url.path == "/v25.0/act_123456"
            assert http_request.url.params["fields"] == "id,account_id,currency,spend_cap"
            return httpx.Response(
                200,
                json={"id": "act_123456", "account_id": "123456", "currency": "USD", "spend_cap": "400000"},
                headers={"x-fb-trace-id": "read-trace"},
            )
        assert http_request.method == "POST"
        assert http_request.url.path == "/v25.0/act_123456"
        assert parse_qs(http_request.content.decode("ascii")) == {"spend_cap": ["500000"]}
        return httpx.Response(200, json={"success": True}, headers={"x-fb-trace-id": "write-trace"})

    adapter, client = make_adapter(handler)
    try:
        snapshot = adapter.read_budget(request)
        receipt = adapter.write_budget(request)
    finally:
        client.close()

    assert snapshot.amount_minor == 400_000
    assert snapshot.currency == "USD"
    assert snapshot.provider_trace_id == "read-trace"
    assert receipt.accepted is True
    assert receipt.provider_trace_id == "write-trace"
    assert [call.method for call in calls] == ["GET", "POST"]


def test_campaign_budget_read_verifies_target_account_and_live_currency():
    request = make_request()
    paths = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        paths.append(http_request.url.path)
        if http_request.url.path.endswith("/777"):
            return httpx.Response(
                200,
                json={
                    "id": "777",
                    "account_id": "123456",
                    "daily_budget": "1000",
                    "lifetime_budget": "0",
                },
            )
        return httpx.Response(
            200,
            json={"id": "act_123456", "account_id": "123456", "currency": "USD"},
        )

    adapter, client = make_adapter(handler)
    try:
        snapshot = adapter.read_budget(request)
    finally:
        client.close()

    assert snapshot.amount_minor == 1000
    assert snapshot.provider_account_id == "123456"
    assert paths == ["/v25.0/777", "/v25.0/act_123456"]


def test_ad_set_budget_is_rejected_when_campaign_controls_budget():
    request = make_request(
        target_type=BudgetTargetType.AD_SET,
        provider_target_id="888",
    )
    paths = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        paths.append(http_request.url.path)
        if http_request.url.path.endswith("/888"):
            return httpx.Response(
                200,
                json={
                    "id": "888",
                    "account_id": "123456",
                    "campaign_id": "777",
                    "daily_budget": "0",
                    "lifetime_budget": "0",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "777",
                "account_id": "123456",
                "daily_budget": "10000",
                "lifetime_budget": "0",
            },
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.read_budget(request)
    finally:
        client.close()

    assert exc_info.value.code == "meta_ad_set_budget_controlled_by_campaign"
    assert paths == ["/v25.0/888", "/v25.0/777"]


def test_ad_set_budget_read_succeeds_only_when_campaign_does_not_own_budget():
    request = make_request(
        target_type=BudgetTargetType.AD_SET,
        provider_target_id="888",
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path.endswith("/888"):
            return httpx.Response(
                200,
                json={
                    "id": "888",
                    "account_id": "123456",
                    "campaign_id": "777",
                    "daily_budget": "1000",
                    "lifetime_budget": "0",
                },
            )
        if http_request.url.path.endswith("/777"):
            return httpx.Response(
                200,
                json={
                    "id": "777",
                    "account_id": "123456",
                    "daily_budget": "0",
                    "lifetime_budget": "0",
                },
            )
        return httpx.Response(
            200,
            json={"id": "act_123456", "account_id": "123456", "currency": "USD"},
        )

    adapter, client = make_adapter(handler)
    try:
        snapshot = adapter.read_budget(request)
    finally:
        client.close()

    assert snapshot.amount_minor == 1000
    assert snapshot.target_type == BudgetTargetType.AD_SET


def test_ad_set_budget_read_accepts_parent_campaign_with_omitted_unset_budgets():
    request = make_request(target_type=BudgetTargetType.AD_SET, provider_target_id="888")

    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path.endswith("/888"):
            return httpx.Response(
                200,
                json={
                    "id": "888",
                    "account_id": "123456",
                    "campaign_id": "777",
                    "daily_budget": "1000",
                },
            )
        if http_request.url.path.endswith("/777"):
            return httpx.Response(200, json={"id": "777", "account_id": "123456"})
        return httpx.Response(
            200,
            json={"id": "act_123456", "account_id": "123456", "currency": "USD"},
        )

    adapter, client = make_adapter(handler)
    try:
        snapshot = adapter.read_budget(request)
    finally:
        client.close()
    assert snapshot.amount_minor == 1000


def test_exact_target_proof_cannot_relabel_an_ad_set_id_as_campaign():
    request = make_request(target_type=BudgetTargetType.CAMPAIGN, provider_target_id="888")
    calls = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request)
        assert http_request.url.path == "/v25.0/act_123456/campaigns"
        assert http_request.url.params["limit"] == "2"
        assert http_request.url.params["filtering"] == '[{"field":"id","operator":"IN","value":["888"]}]'
        return httpx.Response(200, json={"data": []})

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(BudgetConflictError) as exc_info:
            adapter.verify_budget_target(request)
    finally:
        client.close()
    assert exc_info.value.code == "meta_budget_target_not_editable"
    assert len(calls) == 1


def test_campaign_budget_mode_switch_is_never_implicit():
    request = make_request(field=BudgetField.DAILY_BUDGET)

    def handler(_http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "777",
                "account_id": "123456",
                "daily_budget": "0",
                "lifetime_budget": "9000",
            },
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.read_budget(request)
    finally:
        client.close()

    assert exc_info.value.code == "meta_budget_mode_switch_required"


def test_campaign_read_accepts_missing_unset_alternate_budget_field():
    request = make_request()

    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path.endswith("/777"):
            return httpx.Response(
                200,
                json={
                    "id": "777",
                    "account_id": "123456",
                    "daily_budget": "1000",
                    # Meta may omit an unset lifetime_budget entirely.
                },
            )
        return httpx.Response(
            200,
            json={"id": "act_123456", "account_id": "123456", "currency": "USD"},
        )

    adapter, client = make_adapter(handler)
    try:
        snapshot = adapter.read_budget(request)
    finally:
        client.close()
    assert snapshot.amount_minor == 1000


def test_provider_target_from_another_account_is_fail_closed():
    request = make_request()

    def handler(_http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "777",
                "account_id": "999999",
                "daily_budget": "1000",
                "lifetime_budget": "0",
            },
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.read_budget(request)
    finally:
        client.close()

    assert exc_info.value.code == "meta_account_mismatch"


def test_non_integer_provider_budget_is_rejected():
    request = make_request()

    def handler(_http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "777",
                "account_id": "123456",
                "daily_budget": 10.5,
                "lifetime_budget": "0",
            },
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.read_budget(request)
    finally:
        client.close()

    assert exc_info.value.code == "meta_budget_value_invalid"


def test_meta_error_is_sanitized_without_raw_response_or_token():
    request = make_request(
        target_type=BudgetTargetType.ACCOUNT,
        provider_target_id="123456",
        field=BudgetField.SPEND_CAP,
    )

    def handler(_http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": 190,
                    "error_subcode": 463,
                    "message": f"Expired access_token={TOKEN} Bearer second-secret",
                    "fbtrace_id": "trace-190",
                }
            },
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.read_budget(request)
    finally:
        client.close()

    error = exc_info.value.provider_error
    assert error.code == "190"
    assert error.subcode == "463"
    assert error.trace_id == "trace-190"
    assert TOKEN not in error.message
    assert "second-secret" not in error.message
    assert "[REDACTED]" in error.message


@pytest.mark.parametrize(
    ("provider_code", "provider_subcode"),
    [
        ("access_token=provider-secret", "463&access_token=provider-secret"),
        ("12345678901", "99999999999"),
        (True, False),
    ],
)
def test_meta_error_code_and_subcode_are_strict_bounded_numeric_values(
    provider_code,
    provider_subcode,
):
    request = make_request(
        target_type=BudgetTargetType.ACCOUNT,
        provider_target_id="123456",
        field=BudgetField.SPEND_CAP,
    )

    def handler(_http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": provider_code,
                    "error_subcode": provider_subcode,
                    "message": "Meta rejected the request",
                }
            },
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.read_budget(request)
    finally:
        client.close()

    error = exc_info.value.provider_error
    assert error.code == "meta_http_400"
    assert error.subcode is None
    assert "provider-secret" not in error.code
    assert "provider-secret" not in str(error.subcode)


def test_post_transport_timeout_is_unknown_not_a_retry_signal():
    request = make_request()

    def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out after send", request=http_request)

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderWriteOutcomeUnknown) as exc_info:
            adapter.write_budget(request)
    finally:
        client.close()

    assert exc_info.value.code == "meta_write_transport_unknown"
    assert exc_info.value.provider_error.retryable is False


def test_post_server_error_and_unconfirmed_success_are_unknown():
    request = make_request()
    responses = iter(
        [
            httpx.Response(500, json={"error": {"message": "server error"}}),
            httpx.Response(200, json={"id": "777"}),
        ]
    )

    adapter, client = make_adapter(lambda _request: next(responses))
    try:
        with pytest.raises(ProviderWriteOutcomeUnknown) as server_error:
            adapter.write_budget(request)
        with pytest.raises(ProviderWriteOutcomeUnknown) as no_ack:
            adapter.write_budget(request)
    finally:
        client.close()

    assert server_error.value.code == "meta_write_http_unknown"
    assert no_ack.value.code == "meta_write_ack_unknown"


def test_post_4xx_is_a_definitive_sanitized_rejection():
    request = make_request()

    def handler(_http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": 100, "message": "Invalid daily_budget", "fbtrace_id": "bad-field"}},
        )

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(ProviderBudgetError) as exc_info:
            adapter.write_budget(request)
    finally:
        client.close()

    assert exc_info.value.code == "100"
    assert exc_info.value.provider_error.trace_id == "bad-field"
    assert exc_info.value.provider_error.retryable is False
