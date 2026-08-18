from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from app.services.meta_version import meta_graph_api_version
from app.services.provider_budget_commands import (
    BudgetChangeRequest,
    BudgetField,
    BudgetSnapshot,
    BudgetTargetType,
    ProviderBudgetError,
    ProviderWriteOutcomeUnknown,
    ProviderWriteReceipt,
    sanitize_provider_message,
)


def _normalize_account_id(value: object) -> str:
    return str(value or "").strip().removeprefix("act_")


def _trace_id(response: httpx.Response, payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
    body = payload or {}
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("fbtrace_id"):
        candidate = str(error["fbtrace_id"])
        return candidate if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate) else None
    for header in ("x-fb-trace-id", "x-fb-rev"):
        value = response.headers.get(header)
        if value:
            candidate = str(value)
            return candidate if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate) else None
    return None


class MetaBudgetAdapter:
    """Allowlisted Meta budget reads and writes for one explicit access token.

    This adapter is deliberately not imported or instantiated by the application
    routes. A caller must supply a previously authorized, tenant-bound credential.
    """

    def __init__(
        self,
        *,
        access_token: str,
        graph_version: Optional[str] = None,
        app_secret: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 20.0,
    ):
        token = str(access_token or "").strip()
        if not token:
            raise ProviderBudgetError("meta_access_token_missing", "Explicit Meta access token is required")
        self._access_token = token
        self._graph_version = str(graph_version or meta_graph_api_version()).strip()
        if not re.fullmatch(r"v[0-9]+\.0", self._graph_version):
            raise ProviderBudgetError("meta_graph_version_invalid", "Meta Graph API version is invalid")
        self._app_secret = str(app_secret or "").strip() or None
        self._client = client
        self._timeout_seconds = float(timeout_seconds)

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self._graph_version}"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    def _appsecret_proof(self) -> Optional[str]:
        if not self._app_secret:
            return None
        return hmac.new(
            self._app_secret.encode("utf-8"),
            self._access_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        kwargs.setdefault("timeout", self._timeout_seconds)
        if self._client is not None:
            return self._client.request(method, url, **kwargs)
        return httpx.request(method, url, **kwargs)

    def _safe_error(
        self,
        response: httpx.Response,
        *,
        fallback_code: str,
        fallback_message: str,
    ) -> ProviderBudgetError:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        error = error if isinstance(error, dict) else {}
        code = str(error.get("code") or fallback_code)
        subcode = error.get("error_subcode")
        message = error.get("error_user_msg") or error.get("message") or fallback_message
        retryable = response.status_code == 429 or code in {"4", "17", "32", "613"}
        exc = ProviderBudgetError(
            code,
            sanitize_provider_message(
                message,
                secrets_to_redact=tuple(x for x in (self._access_token, self._app_secret or "") if x),
            ),
            subcode=str(subcode) if subcode is not None else None,
            trace_id=_trace_id(response, payload if isinstance(payload, dict) else None),
            retryable=retryable,
        )
        return exc

    def _get_object(self, object_id: str, fields: str) -> tuple[Dict[str, Any], Optional[str]]:
        params: Dict[str, str] = {"fields": fields}
        proof = self._appsecret_proof()
        if proof:
            params["appsecret_proof"] = proof
        url = f"{self.base_url}/{object_id}"
        try:
            response = self._request("GET", url, params=params, headers=self._headers())
        except httpx.RequestError as exc:
            raise ProviderBudgetError(
                "meta_read_transport_error",
                sanitize_provider_message(
                    exc,
                    secrets_to_redact=tuple(x for x in (self._access_token, self._app_secret or "") if x),
                ),
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise self._safe_error(
                response,
                fallback_code=f"meta_http_{response.status_code}",
                fallback_message="Meta rejected the budget read",
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderBudgetError("meta_invalid_json", "Meta returned an invalid budget response") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise self._safe_error(
                response,
                fallback_code="meta_invalid_response",
                fallback_message="Meta returned an invalid budget response",
            )
        return payload, _trace_id(response, payload)

    @staticmethod
    def _minor_value(payload: Dict[str, Any], field: BudgetField) -> int:
        value = payload.get(field.value)
        if field.value not in payload or value is None or value == "":
            raise ProviderBudgetError(
                "meta_budget_value_missing",
                f"Meta did not return the requested {field.value} value",
            )
        if type(value) is int and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise ProviderBudgetError(
            "meta_budget_value_invalid",
            f"Meta returned a non-integer {field.value} value",
        )

    @staticmethod
    def _assert_object_identity(payload: Dict[str, Any], expected_id: str, object_label: str) -> None:
        actual = _normalize_account_id(payload.get("id"))
        if actual != expected_id:
            raise ProviderBudgetError(
                "meta_object_mismatch",
                f"Meta returned a different {object_label} object",
            )

    @staticmethod
    def _assert_account_identity(payload: Dict[str, Any], expected_account_id: str) -> None:
        actual = _normalize_account_id(payload.get("account_id") or payload.get("id"))
        if actual != expected_account_id:
            raise ProviderBudgetError(
                "meta_account_mismatch",
                "Meta target does not belong to the selected advertising account",
            )

    def _read_account_currency(self, account_id: str) -> tuple[str, Optional[str]]:
        payload, trace_id = self._get_object(f"act_{account_id}", "id,account_id,currency")
        self._assert_account_identity(payload, account_id)
        currency = str(payload.get("currency") or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ProviderBudgetError("meta_currency_invalid", "Meta account currency is missing or invalid")
        return currency, trace_id

    @staticmethod
    def _reject_implicit_mode_switch(payload: Dict[str, Any], requested_field: BudgetField) -> None:
        alternate = (
            BudgetField.LIFETIME_BUDGET
            if requested_field == BudgetField.DAILY_BUDGET
            else BudgetField.DAILY_BUDGET
        )
        if MetaBudgetAdapter._minor_value(payload, alternate) > 0:
            raise ProviderBudgetError(
                "meta_budget_mode_switch_required",
                "Switching between daily and lifetime budget requires a separate explicit workflow",
            )

    def read_budget(self, request: BudgetChangeRequest) -> BudgetSnapshot:
        observed_at = datetime.now(timezone.utc)

        if request.target_type == BudgetTargetType.ACCOUNT:
            payload, trace_id = self._get_object(
                f"act_{request.provider_account_id}",
                "id,account_id,currency,spend_cap",
            )
            self._assert_account_identity(payload, request.provider_account_id)
            currency = str(payload.get("currency") or "").strip().upper()
            if len(currency) != 3 or not currency.isalpha():
                raise ProviderBudgetError("meta_currency_invalid", "Meta account currency is missing or invalid")
            amount = self._minor_value(payload, BudgetField.SPEND_CAP)
        elif request.target_type == BudgetTargetType.CAMPAIGN:
            payload, trace_id = self._get_object(
                request.provider_target_id,
                "id,account_id,daily_budget,lifetime_budget",
            )
            self._assert_object_identity(payload, request.provider_target_id, "campaign")
            self._assert_account_identity(payload, request.provider_account_id)
            self._reject_implicit_mode_switch(payload, request.field)
            amount = self._minor_value(payload, request.field)
            currency, account_trace = self._read_account_currency(request.provider_account_id)
            trace_id = account_trace or trace_id
        elif request.target_type == BudgetTargetType.AD_SET:
            payload, trace_id = self._get_object(
                request.provider_target_id,
                "id,account_id,campaign_id,daily_budget,lifetime_budget",
            )
            self._assert_object_identity(payload, request.provider_target_id, "ad set")
            self._assert_account_identity(payload, request.provider_account_id)
            campaign_id = str(payload.get("campaign_id") or "").strip()
            if not campaign_id.isdigit():
                raise ProviderBudgetError("meta_campaign_missing", "Meta ad set campaign identity is missing")
            campaign, campaign_trace = self._get_object(
                campaign_id,
                "id,account_id,daily_budget,lifetime_budget",
            )
            self._assert_object_identity(campaign, campaign_id, "campaign")
            self._assert_account_identity(campaign, request.provider_account_id)
            if (
                self._minor_value(campaign, BudgetField.DAILY_BUDGET) > 0
                or self._minor_value(campaign, BudgetField.LIFETIME_BUDGET) > 0
            ):
                raise ProviderBudgetError(
                    "meta_ad_set_budget_controlled_by_campaign",
                    "Ad set budget is controlled by its campaign and cannot be changed independently",
                )
            self._reject_implicit_mode_switch(payload, request.field)
            amount = self._minor_value(payload, request.field)
            currency, account_trace = self._read_account_currency(request.provider_account_id)
            trace_id = account_trace or campaign_trace or trace_id
        else:  # BudgetChangeRequest validation makes this unreachable.
            raise ProviderBudgetError("meta_target_unsupported", "Unsupported Meta budget target")

        return BudgetSnapshot(
            target_type=request.target_type,
            provider_target_id=request.provider_target_id,
            provider_account_id=request.provider_account_id,
            field=request.field,
            amount_minor=amount,
            currency=currency,
            observed_at=observed_at,
            provider_trace_id=trace_id,
        )

    def write_budget(self, request: BudgetChangeRequest) -> ProviderWriteReceipt:
        object_id = (
            f"act_{request.provider_account_id}"
            if request.target_type == BudgetTargetType.ACCOUNT
            else request.provider_target_id
        )
        url = f"{self.base_url}/{object_id}"
        data = {request.field.value: str(request.amount_minor)}
        proof = self._appsecret_proof()
        if proof:
            data["appsecret_proof"] = proof
        try:
            response = self._request("POST", url, data=data, headers=self._headers())
        except httpx.RequestError as exc:
            raise ProviderWriteOutcomeUnknown(
                "meta_write_transport_unknown",
                sanitize_provider_message(
                    exc,
                    secrets_to_redact=tuple(x for x in (self._access_token, self._app_secret or "") if x),
                ),
            ) from exc

        try:
            payload = response.json()
        except Exception:
            payload = {}
        trace_id = _trace_id(response, payload if isinstance(payload, dict) else None)

        if response.status_code >= 500:
            raise ProviderWriteOutcomeUnknown(
                "meta_write_http_unknown",
                "Meta returned a server error after the budget request was sent",
                trace_id=trace_id,
            )
        if response.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
            raise self._safe_error(
                response,
                fallback_code=f"meta_http_{response.status_code}",
                fallback_message="Meta rejected the budget change",
            )
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ProviderWriteOutcomeUnknown(
                "meta_write_ack_unknown",
                "Meta response did not confirm the budget change",
                trace_id=trace_id,
            )
        return ProviderWriteReceipt(accepted=True, provider_trace_id=trace_id)
