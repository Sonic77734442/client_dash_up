from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import httpx

from app.services.meta_version import meta_graph_api_version
from app.services.provider_budget_commands import (
    BudgetChangeRequest,
    BudgetConflictError,
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


_META_PROVIDER_ERROR_NUMBER = re.compile(r"[0-9]{1,10}")
_INTERNAL_PROVIDER_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _safe_meta_error_code(value: object, *, fallback: str) -> str:
    """Keep only bounded numeric Meta codes; otherwise use a server-owned code."""

    candidate = "" if isinstance(value, bool) else str(value or "").strip()
    if _META_PROVIDER_ERROR_NUMBER.fullmatch(candidate):
        return candidate
    safe_fallback = str(fallback or "").strip().lower()
    return safe_fallback if _INTERNAL_PROVIDER_ERROR_CODE.fullmatch(safe_fallback) else "meta_provider_error"


def _safe_meta_error_subcode(value: object) -> Optional[str]:
    candidate = "" if isinstance(value, bool) else str(value or "").strip()
    return candidate if _META_PROVIDER_ERROR_NUMBER.fullmatch(candidate) else None


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


@dataclass(frozen=True)
class MetaBudgetTargetFieldSnapshot:
    field: BudgetField
    current_minor: int
    currency: str
    observed_at: datetime
    editable: bool = True
    reason_code: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class MetaBudgetTargetSnapshot:
    target_type: BudgetTargetType
    provider_target_id: str
    name: str
    status: str
    budget_fields: Tuple[MetaBudgetTargetFieldSnapshot, ...]


class MetaBudgetAdapter:
    """Allowlisted Meta budget reads and writes for one explicit access token.

    Application routes instantiate this adapter only after resolving an explicit,
    encrypted, tenant-bound credential and rechecking provider permissions.
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
        code = _safe_meta_error_code(error.get("code"), fallback=fallback_code)
        subcode = _safe_meta_error_subcode(error.get("error_subcode"))
        message = error.get("error_user_msg") or error.get("message") or fallback_message
        retryable = response.status_code == 429 or code in {"4", "17", "32", "613"}
        exc = ProviderBudgetError(
            code,
            sanitize_provider_message(
                message,
                secrets_to_redact=tuple(x for x in (self._access_token, self._app_secret or "") if x),
            ),
            subcode=subcode,
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

    def _get_collection(
        self,
        *,
        account_id: str,
        edge: str,
        fields: str,
        max_items: int = 1000,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """Read one allowlisted account edge without following arbitrary URLs.

        Meta pagination links can contain access tokens.  We validate the next
        link's exact Graph host/version/account/edge, extract only its opaque
        cursor, then issue the next request against our own fixed endpoint with
        the token kept in the Authorization header.
        """
        if edge not in {"campaigns", "adsets"}:
            raise ProviderBudgetError("meta_edge_not_allowed", "Unsupported Meta budget target edge")
        normalized_account_id = _normalize_account_id(account_id)
        if not normalized_account_id.isdigit():
            raise ProviderBudgetError("meta_account_invalid", "Meta advertising account id is invalid")
        endpoint_path = f"/{self._graph_version}/act_{normalized_account_id}/{edge}"
        url = f"https://graph.facebook.com{endpoint_path}"
        after: Optional[str] = None
        items: List[Dict[str, Any]] = []
        for _page in range(max_pages):
            params: Dict[str, str] = {"fields": fields, "limit": "100"}
            if after:
                params["after"] = after
            proof = self._appsecret_proof()
            if proof:
                params["appsecret_proof"] = proof
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
                    fallback_message="Meta rejected the budget target read",
                )
            try:
                payload = response.json()
            except Exception as exc:
                raise ProviderBudgetError("meta_invalid_json", "Meta returned an invalid target list") from exc
            if not isinstance(payload, dict) or payload.get("error"):
                raise self._safe_error(
                    response,
                    fallback_code="meta_invalid_response",
                    fallback_message="Meta returned an invalid target list",
                )
            page_items = payload.get("data")
            if not isinstance(page_items, list) or any(not isinstance(item, dict) for item in page_items):
                raise ProviderBudgetError("meta_invalid_response", "Meta returned an invalid target list")
            items.extend(page_items)
            if len(items) > max_items:
                raise ProviderBudgetError("meta_target_limit_exceeded", "Meta returned too many budget targets")

            paging = payload.get("paging")
            next_url = paging.get("next") if isinstance(paging, dict) else None
            if not next_url:
                return items
            parsed = urlsplit(str(next_url))
            if (
                parsed.scheme != "https"
                or parsed.netloc != "graph.facebook.com"
                or parsed.path.rstrip("/") != endpoint_path
            ):
                raise ProviderBudgetError("meta_pagination_invalid", "Meta returned an unsafe pagination link")
            cursors = parse_qs(parsed.query, keep_blank_values=False).get("after") or []
            candidate = str(cursors[0] if cursors else "").strip()
            if not candidate or len(candidate) > 2048 or not re.fullmatch(r"[A-Za-z0-9._~-]+", candidate):
                raise ProviderBudgetError("meta_pagination_invalid", "Meta returned an invalid pagination cursor")
            after = candidate
        raise ProviderBudgetError("meta_pagination_limit", "Meta target pagination exceeded the safe page limit")

    def _get_exact_edge_object(
        self,
        *,
        account_id: str,
        edge: str,
        target_id: str,
        fields: str,
    ) -> Dict[str, Any]:
        """Prove an object's exact Meta type and account edge in one bounded GET.

        Unlike the UI listing method this never paginates. The endpoint, filter,
        limit and requested fields are all server-built, so an ad-set identifier
        cannot be relabelled as a campaign and a very large account cannot extend
        the write saga beyond the target lease.
        """

        if edge not in {"campaigns", "adsets"}:
            raise ProviderBudgetError("meta_edge_not_allowed", "Unsupported Meta budget target edge")
        normalized_account_id = _normalize_account_id(account_id)
        normalized_target_id = str(target_id or "").strip()
        if not normalized_account_id.isdigit() or not normalized_target_id.isdigit():
            raise ProviderBudgetError("meta_object_invalid", "Meta budget target identity is invalid")
        url = f"{self.base_url}/act_{normalized_account_id}/{edge}"
        params: Dict[str, str] = {
            "fields": fields,
            "limit": "2",
            "filtering": json.dumps(
                [{"field": "id", "operator": "IN", "value": [normalized_target_id]}],
                separators=(",", ":"),
            ),
        }
        proof = self._appsecret_proof()
        if proof:
            params["appsecret_proof"] = proof
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
                fallback_message="Meta rejected the exact budget target read",
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderBudgetError("meta_invalid_json", "Meta returned an invalid exact target response") from exc
        rows = payload.get("data") if isinstance(payload, dict) and not payload.get("error") else None
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise ProviderBudgetError("meta_invalid_response", "Meta returned an invalid exact target response")
        exact = [item for item in rows if str(item.get("id") or "").strip() == normalized_target_id]
        if len(rows) != 1 or len(exact) != 1:
            raise BudgetConflictError(
                "meta_budget_target_not_editable",
                "The selected object is not an editable target of the declared type in this Meta account",
            )
        return exact[0]

    @staticmethod
    def _optional_minor_value(payload: Dict[str, Any], field: BudgetField) -> int:
        value = payload.get(field.value, 0)
        if value in (None, ""):
            return 0
        if type(value) is int and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise ProviderBudgetError(
            "meta_budget_value_invalid",
            f"Meta returned a non-integer {field.value} value",
        )

    @classmethod
    def _effective_budget_field(cls, payload: Dict[str, Any]) -> Optional[tuple[BudgetField, int]]:
        daily = cls._optional_minor_value(payload, BudgetField.DAILY_BUDGET)
        lifetime = cls._optional_minor_value(payload, BudgetField.LIFETIME_BUDGET)
        if daily > 0 and lifetime > 0:
            raise ProviderBudgetError(
                "meta_budget_mode_ambiguous",
                "Meta returned both daily and lifetime budgets for one target",
            )
        if daily > 0:
            return BudgetField.DAILY_BUDGET, daily
        if lifetime > 0:
            return BudgetField.LIFETIME_BUDGET, lifetime
        return None

    def list_budget_targets(self, provider_account_id: str) -> List[MetaBudgetTargetSnapshot]:
        """List effective campaign/ad-set budget owners for one explicit account."""
        account_id = _normalize_account_id(provider_account_id)
        if not account_id.isdigit():
            raise ProviderBudgetError("meta_account_invalid", "Meta advertising account id is invalid")
        currency, _trace = self._read_account_currency(account_id)
        campaigns = self._get_collection(
            account_id=account_id,
            edge="campaigns",
            fields="id,name,status,effective_status,account_id,daily_budget,lifetime_budget",
        )
        campaign_budget_owner: Dict[str, bool] = {}
        observed_at = datetime.now(timezone.utc)
        targets: List[MetaBudgetTargetSnapshot] = []
        for item in campaigns:
            target_id = str(item.get("id") or "").strip()
            if not target_id.isdigit():
                raise ProviderBudgetError("meta_object_invalid", "Meta returned an invalid campaign id")
            self._assert_account_identity(item, account_id)
            effective = self._effective_budget_field(item)
            campaign_budget_owner[target_id] = effective is not None
            if effective is None:
                continue
            field, current = effective
            targets.append(
                MetaBudgetTargetSnapshot(
                    target_type=BudgetTargetType.CAMPAIGN,
                    provider_target_id=target_id,
                    name=str(item.get("name") or f"Campaign {target_id}"),
                    status=str(item.get("effective_status") or item.get("status") or "UNKNOWN"),
                    budget_fields=(
                        MetaBudgetTargetFieldSnapshot(
                            field=field,
                            current_minor=current,
                            currency=currency,
                            observed_at=observed_at,
                        ),
                    ),
                )
            )

        ad_sets = self._get_collection(
            account_id=account_id,
            edge="adsets",
            fields="id,name,status,effective_status,account_id,campaign_id,daily_budget,lifetime_budget",
        )
        for item in ad_sets:
            target_id = str(item.get("id") or "").strip()
            campaign_id = str(item.get("campaign_id") or "").strip()
            if not target_id.isdigit() or not campaign_id.isdigit():
                raise ProviderBudgetError("meta_object_invalid", "Meta returned an invalid ad set identity")
            self._assert_account_identity(item, account_id)
            if campaign_id not in campaign_budget_owner:
                raise ProviderBudgetError(
                    "meta_campaign_scope_mismatch",
                    "Meta returned an ad set outside the enumerated account campaigns",
                )
            if campaign_budget_owner[campaign_id]:
                continue
            effective = self._effective_budget_field(item)
            if effective is None:
                continue
            field, current = effective
            targets.append(
                MetaBudgetTargetSnapshot(
                    target_type=BudgetTargetType.AD_SET,
                    provider_target_id=target_id,
                    name=str(item.get("name") or f"Ad set {target_id}"),
                    status=str(item.get("effective_status") or item.get("status") or "UNKNOWN"),
                    budget_fields=(
                        MetaBudgetTargetFieldSnapshot(
                            field=field,
                            current_minor=current,
                            currency=currency,
                            observed_at=observed_at,
                        ),
                    ),
                )
            )
        targets.sort(key=lambda item: (item.target_type.value, item.name.casefold(), item.provider_target_id))
        return targets

    def verify_budget_target(self, request: BudgetChangeRequest) -> None:
        """Bounded pre-write proof of exact type, ownership and budget mode."""

        if request.target_type == BudgetTargetType.ACCOUNT:
            # Account controls are separately feature-gated and read_budget uses
            # the typed act_<id> endpoint.
            self.read_budget(request)
            return
        if request.target_type == BudgetTargetType.CAMPAIGN:
            payload = self._get_exact_edge_object(
                account_id=request.provider_account_id,
                edge="campaigns",
                target_id=request.provider_target_id,
                fields="id,account_id,daily_budget,lifetime_budget",
            )
            self._assert_account_identity(payload, request.provider_account_id)
            effective = self._effective_budget_field(payload)
            if effective is None or effective[0] != request.field:
                raise BudgetConflictError(
                    "meta_budget_target_not_editable",
                    "The campaign is not the effective owner of the selected budget field",
                )
            return

        payload = self._get_exact_edge_object(
            account_id=request.provider_account_id,
            edge="adsets",
            target_id=request.provider_target_id,
            fields="id,account_id,campaign_id,daily_budget,lifetime_budget",
        )
        self._assert_account_identity(payload, request.provider_account_id)
        campaign_id = str(payload.get("campaign_id") or "").strip()
        if not campaign_id.isdigit():
            raise ProviderBudgetError("meta_campaign_missing", "Meta ad set campaign identity is missing")
        campaign, _trace = self._get_object(
            campaign_id,
            "id,account_id,daily_budget,lifetime_budget",
        )
        self._assert_object_identity(campaign, campaign_id, "campaign")
        self._assert_account_identity(campaign, request.provider_account_id)
        if self._effective_budget_field(campaign) is not None:
            raise BudgetConflictError(
                "meta_ad_set_budget_controlled_by_campaign",
                "Ad set budget is controlled by its campaign and cannot be changed independently",
            )
        effective = self._effective_budget_field(payload)
        if effective is None or effective[0] != request.field:
            raise BudgetConflictError(
                "meta_budget_target_not_editable",
                "The ad set is not the effective owner of the selected budget field",
            )

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
        if MetaBudgetAdapter._optional_minor_value(payload, alternate) > 0:
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
            if self._effective_budget_field(campaign) is not None:
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
