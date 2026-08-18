import json
import logging
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _reset_as_admin() -> None:
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={
            "email": "validation-redaction-admin@test.local",
            "name": "Validation redaction admin",
            "role": "admin",
            "status": "active",
        },
    )
    assert admin.status_code == 200
    session = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": admin.json()["id"], "ttl_minutes": 60},
    )
    assert session.status_code == 200
    client.headers.update({"Authorization": f"Bearer {session.json()['token']}"})


def _assert_sanitized_validation_error(response, marker: str, caplog, capsys) -> None:
    assert response.status_code == 422
    assert marker not in response.text
    assert marker not in "\n".join(record.getMessage() for record in caplog.records)
    assert marker not in capsys.readouterr().out

    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Validation failed"
    errors = body["error"]["details"]["errors"]
    assert errors
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    serialized_errors = json.dumps(errors)
    for forbidden_key in ('"input"', '"ctx"', '"url"'):
        assert forbidden_key not in serialized_errors


def test_model_validation_does_not_echo_integration_access_token(caplog, capsys):
    _reset_as_admin()
    marker = "ACCESS_TOKEN_MUST_NEVER_APPEAR_8f6bd3b2"
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    response = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "meta",
            "scope_type": "global",
            "scope_id": str(uuid4()),
            "credentials": {"access_token": marker},
        },
    )

    _assert_sanitized_validation_error(response, marker, caplog, capsys)


def test_missing_provider_config_field_does_not_echo_client_secret(caplog, capsys):
    _reset_as_admin()
    marker = "CLIENT_SECRET_MUST_NEVER_APPEAR_2f48080a"
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    response = client.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "public-client-id",
            "client_secret": marker,
        },
    )

    _assert_sanitized_validation_error(response, marker, caplog, capsys)


def test_missing_budget_field_does_not_echo_preview_token(caplog, capsys):
    _reset_as_admin()
    marker = "PREVIEW_TOKEN_MUST_NEVER_APPEAR_" + ("7" * 64)
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    response = client.post(
        "/provider-controls/meta/budget-changes",
        headers={"Idempotency-Key": "validation-redaction-test"},
        json={
            "client_id": str(uuid4()),
            "ad_account_id": str(uuid4()),
            "target_type": "campaign",
            "provider_target_id": "123456",
            "field": "daily_budget",
            "amount_minor": 1000,
            "reason": "Regression test",
            "confirm": True,
            "preview_token": marker,
        },
    )

    _assert_sanitized_validation_error(response, marker, caplog, capsys)


def test_input_derived_extra_field_name_is_replaced_in_location(caplog, capsys):
    _reset_as_admin()
    marker = "SECRET_INPUT_KEY_MUST_NEVER_APPEAR_52d57d5a"
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    response = client.post(
        "/provider-controls/meta/budget-changes",
        headers={"Idempotency-Key": "validation-location-redaction-test"},
        json={
            "client_id": str(uuid4()),
            "ad_account_id": str(uuid4()),
            "target_type": "campaign",
            "provider_target_id": "123456",
            "field": "daily_budget",
            "amount_minor": 1000,
            "currency": "USD",
            "reason": "Regression test",
            "confirm": True,
            "preview_token": "x" * 80,
            marker: "attacker-controlled field",
        },
    )

    _assert_sanitized_validation_error(response, marker, caplog, capsys)
    assert response.json()["error"]["details"]["errors"] == [
        {"type": "extra_forbidden", "loc": ["body", "field"], "msg": "Invalid value"}
    ]
