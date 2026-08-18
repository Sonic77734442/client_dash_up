from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from typing import Callable, Dict, List, Optional, Protocol
from uuid import UUID

from fastapi import HTTPException

from app.schemas import AdAccountCreate, AdAccountDiscoverResponse, AdAccountOut, AdAccountPatch
from app.services.ad_accounts import AdAccountStore
from app.services.clients import ClientStore
from app.services.meta_connection import (
    MetaCredentialDiagnostic,
    require_usable_validated_meta_credentials,
)
from app.services.providers import google_ads, meta, tiktok


class AccountDiscoverer(Protocol):
    def __call__(self, credentials: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]: ...


@dataclass
class DiscoveryResult:
    requested_provider: str
    client_id: UUID
    discovered: int
    created: int
    updated: int
    skipped: int
    providers_attempted: List[str]
    providers_failed: Dict[str, str]
    items: List[AdAccountOut]


def _normalize_provider(value: Optional[str]) -> str:
    raw = (value or "all").strip().lower()
    if raw in {"", "all"}:
        return "all"
    if raw == "facebook":
        return "meta"
    return raw


def _canonical_external_id(provider: str, value: object) -> str:
    raw = str(value or "").strip()
    p = (provider or "").strip().lower()
    if p == "google":
        normalized = google_ads.normalize_customer_id(raw)
        return normalized or raw
    if p == "meta":
        return raw.replace("act_", "").strip()
    if p == "tiktok":
        return tiktok.normalize_advertiser_id(raw)
    return raw


def _fallback_ids_from_env(env_name: str) -> List[str]:
    raw = os.getenv(env_name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _fallback_meta_accounts() -> List[Dict[str, object]]:
    return [
        {"external_account_id": account_id, "name": f"Meta {account_id}", "currency": "USD"}
        for account_id in _fallback_ids_from_env("META_ACCOUNT_IDS")
    ]


def _fallback_google_accounts() -> List[Dict[str, object]]:
    return [
        {"external_account_id": customer_id, "name": f"Google {customer_id}", "currency": "USD"}
        for customer_id in _fallback_ids_from_env("GOOGLE_CUSTOMER_IDS")
    ]


def _fallback_tiktok_accounts() -> List[Dict[str, object]]:
    return [
        {"external_account_id": advertiser_id, "name": f"TikTok {advertiser_id}", "currency": "USD"}
        for advertiser_id in _fallback_ids_from_env("TIKTOK_ADVERTISER_IDS")
    ]


class AdAccountDiscoveryService:
    def __init__(
        self,
        account_store: AdAccountStore,
        *,
        client_store: Optional[ClientStore] = None,
        discoverers: Optional[Dict[str, AccountDiscoverer]] = None,
        credential_resolver: Optional[
            Callable[[str, UUID, Optional[UUID], Optional[UUID]], Optional[Dict[str, object]]]
        ] = None,
        credential_candidates_resolver: Optional[
            Callable[[str, UUID, Optional[UUID], Optional[UUID]], List[Dict[str, object]]]
        ] = None,
    ):
        self.account_store = account_store
        self.client_store = client_store or getattr(account_store, "client_store", None)
        self.credential_resolver = credential_resolver
        self.credential_candidates_resolver = credential_candidates_resolver
        self.discoverers: Dict[str, AccountDiscoverer] = discoverers or {
            "meta": self._discover_meta_accounts,
            "google": self._discover_google_accounts,
            "tiktok": self._discover_tiktok_accounts,
        }

    @staticmethod
    def _discover_meta_accounts(credentials: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
        try:
            rows = meta.list_accounts(credentials)
            if rows or credentials:
                return rows
        except Exception:
            if credentials:
                raise
        return _fallback_meta_accounts()

    @staticmethod
    def _discover_google_accounts(credentials: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
        try:
            rows = google_ads.list_accounts(credentials)
            if rows or credentials:
                return rows
        except Exception:
            # If tenant-scoped credentials are provided, surface provider errors to caller
            # instead of silently falling back to env IDs (which can mask MCC discovery issues).
            if credentials:
                raise
        return _fallback_google_accounts()

    @staticmethod
    def _discover_tiktok_accounts(credentials: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
        try:
            rows = tiktok.list_accounts(credentials)
            if rows or credentials:
                return rows
        except Exception:
            if credentials:
                raise
        return _fallback_tiktok_accounts()

    @staticmethod
    def _safe_provider_error(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                return str(detail.get("message") or detail.get("code") or "Provider discovery failed")
            return str(detail)
        return str(exc) or "Provider discovery failed"

    @staticmethod
    def _is_assignment_conflict(exc: HTTPException) -> bool:
        detail = exc.detail
        if isinstance(detail, dict):
            return str(detail.get("code") or "").strip().lower() == "assignment_conflict"
        return "assignment_conflict" in str(detail or "").lower()

    @staticmethod
    def _select_meta_candidates_for_tenant(
        candidates: List[Optional[Dict[str, object]]],
        *,
        client_id: UUID,
        user_id: Optional[UUID],
        agency_id: Optional[UUID],
    ) -> List[Optional[Dict[str, object]]]:
        """Keep Meta credentials owned by the selected actor tenant.

        Production resolvers add the private scope fields below. Custom test or
        legacy resolvers without those fields retain their historical behavior.
        """
        tagged = [candidate for candidate in candidates if candidate and "__scope_type" in candidate]
        if not tagged:
            return candidates
        if agency_id is not None:
            return [
                candidate
                for candidate in tagged
                if str(candidate.get("__scope_type") or "") == "agency"
                and str(candidate.get("__scope_id") or "") == str(agency_id)
            ]
        if user_id is not None:
            return [
                candidate
                for candidate in tagged
                if str(candidate.get("__scope_type") or "") == "client"
                and str(candidate.get("__scope_id") or "") == str(client_id)
                and str(candidate.get("__created_by") or "") == str(user_id)
            ]
        # An admin may intentionally discover through client, agency, or global
        # credentials. Every returned row is already bound to this client by the
        # credential store, and each discovered account is pinned to its exact id.
        return tagged

    @staticmethod
    def _provider_credentials(candidate: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
        if candidate is None:
            return None
        return {key: value for key, value in candidate.items() if not key.startswith("__")}

    def discover(
        self,
        *,
        provider: Optional[str],
        client_id: UUID,
        user_id: Optional[UUID] = None,
        agency_id: Optional[UUID] = None,
        upsert_existing: bool = True,
        expected_currency: Optional[str] = None,
    ) -> DiscoveryResult:
        provider_filter = _normalize_provider(provider)
        if provider_filter == "all":
            providers = [p for p in ("meta", "google", "tiktok") if p in self.discoverers]
        else:
            if provider_filter not in self.discoverers:
                raise HTTPException(status_code=400, detail="Unsupported provider for discovery")
            providers = [provider_filter]

        all_accounts = self.account_store.list(status="all")
        existing = {
            (
                str(a.client_id),
                (a.platform or "").lower().strip(),
                _canonical_external_id((a.platform or "").lower().strip(), a.external_account_id),
            ): a
            for a in all_accounts
            if a.client_id == client_id
        }
        active_client_ids = (
            {client.id for client in self.client_store.list(status="active")}
            if self.client_store is not None
            else None
        )
        active_assignments: Dict[tuple[str, str], List[AdAccountOut]] = {}
        for account in all_accounts:
            if account.status != "active":
                continue
            if active_client_ids is not None and account.client_id not in active_client_ids:
                continue
            account_provider = (account.platform or "").lower().strip()
            identity = (account_provider, _canonical_external_id(account_provider, account.external_account_id))
            active_assignments.setdefault(identity, []).append(account)
        now_iso = _utcnow().isoformat()

        created = 0
        updated = 0
        skipped = 0
        discovered = 0
        items: List[AdAccountOut] = []
        providers_failed: Dict[str, str] = {}
        provider_conflicts: Dict[str, int] = {}
        provider_currency_mismatches: Dict[str, int] = {}
        normalized_expected_currency = str(expected_currency or "").strip().upper()

        for p in providers:
            discoverer = self.discoverers.get(p)
            if not discoverer:
                providers_failed[p] = "Provider discovery not configured"
                continue
            provider_runs: List[
                tuple[Optional[str], List[Dict[str, object]], Optional[MetaCredentialDiagnostic]]
            ] = []
            provider_errors: List[str] = []

            candidate_credentials: List[Optional[Dict[str, object]]] = []
            if self.credential_candidates_resolver:
                rows = self.credential_candidates_resolver(p, client_id, user_id, agency_id)
                candidate_credentials = rows if rows else []
            elif self.credential_resolver:
                single = self.credential_resolver(p, client_id, user_id, agency_id)
                candidate_credentials = [single] if single is not None else []
            else:
                candidate_credentials = []
            if user_id is not None:
                candidate_credentials = [candidate for candidate in candidate_credentials if candidate is not None]
            if p == "meta":
                candidate_credentials = self._select_meta_candidates_for_tenant(
                    candidate_credentials,
                    client_id=client_id,
                    user_id=user_id,
                    agency_id=agency_id,
                )
            if not candidate_credentials:
                if user_id is None:
                    # Platform-admin and scheduler runs may intentionally use the
                    # legacy, platform-owned environment credentials.
                    candidate_credentials = [None]
                else:
                    # A tenant-scoped run must never reinterpret an empty
                    # credential resolution as permission to use platform-owned
                    # environment secrets.
                    providers_failed[p] = "Provider credentials are missing or incomplete."
                    continue

            for candidate in candidate_credentials:
                cred_id = str((candidate or {}).get("__credential_id") or "").strip() or None
                provider_credentials = self._provider_credentials(candidate)
                meta_diagnostic: Optional[MetaCredentialDiagnostic] = None
                try:
                    if p == "meta" and provider_credentials:
                        meta_diagnostic = require_usable_validated_meta_credentials(
                            provider_credentials,
                            allow_unverified_legacy=True,
                        )
                    try:
                        rows = discoverer(provider_credentials) or []
                    except TypeError:
                        # Backward-compatible path for tests/custom discoverers without credentials param.
                        rows = discoverer() or []
                    provider_runs.append((cred_id, rows, meta_diagnostic))
                except Exception as exc:
                    provider_errors.append(self._safe_provider_error(exc))

            if not provider_runs and provider_errors:
                providers_failed[p] = provider_errors[-1]
                continue

            seen_meta_credentials: Dict[str, Optional[str]] = {}
            for cred_id, rows, meta_diagnostic in provider_runs:
                for row in rows:
                    external_account_id = _canonical_external_id(p, row.get("external_account_id"))
                    if not external_account_id:
                        skipped += 1
                        continue
                    if p == "meta" and external_account_id in seen_meta_credentials:
                        # The same account can be visible through several Business
                        # connections. Keep the newest resolver result instead of
                        # letting a later credential silently replace its binding.
                        skipped += 1
                        continue
                    if p == "meta":
                        seen_meta_credentials[external_account_id] = cred_id
                    discovered += 1
                    name = str(row.get("name") or f"{p.upper()} {external_account_id}").strip()
                    currency = str(row.get("currency") or "USD").strip().upper() or "USD"
                    if normalized_expected_currency and currency != normalized_expected_currency:
                        skipped += 1
                        provider_currency_mismatches[p] = provider_currency_mismatches.get(p, 0) + 1
                        continue
                    key = (str(client_id), p, external_account_id)
                    existing_account = existing.get(key)
                    conflicting_assignment = next(
                        (
                            account
                            for account in active_assignments.get((p, external_account_id), [])
                            if account.client_id != client_id
                        ),
                        None,
                    )
                    # Preserve legacy duplicates already active in the target client, but never
                    # create/reactivate another active assignment under a different client.
                    if conflicting_assignment and not (existing_account and existing_account.status == "active"):
                        skipped += 1
                        provider_conflicts[p] = provider_conflicts.get(p, 0) + 1
                        continue
                    discovery_meta = {
                        "discovered_at": now_iso,
                        "discovery_provider": p,
                        "discovery_source": str(row.get("source") or "provider_api_or_env_fallback"),
                    }
                    if cred_id:
                        discovery_meta["integration_credential_id"] = cred_id
                    if p == "meta" and meta_diagnostic is not None:
                        discovery_meta["meta_connection_status"] = meta_diagnostic.status
                        discovery_meta["meta_connection_diagnostic_code"] = meta_diagnostic.code
                    if p == "meta":
                        # A successful provider discovery is the only operation
                        # that may rebind an account after it changes clients.
                        discovery_meta["meta_rediscovery_required"] = False
                    if existing_account:
                        merged_meta = dict(existing_account.metadata or {})
                        merged_meta.update(discovery_meta)
                        if not upsert_existing:
                            skipped += 1
                            items.append(existing_account)
                            continue
                        patch_data: Dict[str, object] = {"metadata": merged_meta}
                        if name and name != existing_account.name:
                            patch_data["name"] = name
                        if currency and currency != existing_account.currency:
                            patch_data["currency"] = currency
                        if existing_account.status != "active":
                            patch_data["status"] = "active"
                        patch = AdAccountPatch(**patch_data)
                        try:
                            patched = self.account_store.patch(existing_account.id, patch)
                            existing[key] = patched
                            if patched.status == "active":
                                active_assignments.setdefault((p, external_account_id), []).append(patched)
                            items.append(patched)
                            updated += 1
                        except HTTPException as exc:
                            if exc.status_code == 409:
                                skipped += 1
                                provider_conflicts[p] = provider_conflicts.get(p, 0) + 1
                                continue
                            raise
                        continue

                    try:
                        created_row = self.account_store.create(
                            AdAccountCreate(
                                client_id=client_id,
                                platform=p,
                                external_account_id=external_account_id,
                                name=name,
                                currency=currency,
                                status="active",
                                metadata=discovery_meta,
                            )
                        )
                        existing[key] = created_row
                        active_assignments.setdefault((p, external_account_id), []).append(created_row)
                        items.append(created_row)
                        created += 1
                    except HTTPException as exc:
                        # Conflict-safe upsert fallback: re-read matching account and patch instead of failing discover.
                        if exc.status_code == 409:
                            if self._is_assignment_conflict(exc):
                                skipped += 1
                                provider_conflicts[p] = provider_conflicts.get(p, 0) + 1
                                continue
                            fallback_existing = existing.get(key)
                            if not fallback_existing:
                                refreshed = self.account_store.list(client_id=client_id, status="all")
                                fallback_existing = next(
                                    (
                                        a
                                        for a in refreshed
                                        if str(a.client_id) == str(client_id)
                                        and (a.platform or "").lower().strip() == p
                                        and _canonical_external_id(p, a.external_account_id) == external_account_id
                                    ),
                                    None,
                                )
                            if not fallback_existing:
                                raise
                            merged_meta = dict(fallback_existing.metadata or {})
                            merged_meta.update(discovery_meta)
                            patched = self.account_store.patch(
                                fallback_existing.id,
                                AdAccountPatch(
                                    name=name if name and name != fallback_existing.name else None,
                                    currency=currency if currency and currency != fallback_existing.currency else None,
                                    status="active" if fallback_existing.status != "active" else None,
                                    metadata=merged_meta,
                                ),
                            )
                            existing[key] = patched
                            if patched.status == "active":
                                active_assignments.setdefault((p, external_account_id), []).append(patched)
                            items.append(patched)
                            updated += 1
                            continue
                        raise

            if provider_errors and p not in providers_failed:
                providers_failed[p] = provider_errors[-1]

            # Refresh map after provider batch to absorb possible concurrent updates and prevent stale-key conflicts.
            existing = {
                (
                    str(a.client_id),
                    (a.platform or "").lower().strip(),
                    _canonical_external_id((a.platform or "").lower().strip(), a.external_account_id),
                ): a
                for a in self.account_store.list(client_id=client_id, status="all")
            }

        for p, count in provider_conflicts.items():
            providers_failed[p] = f"assignment_conflict:{count}"
        for p, count in provider_currency_mismatches.items():
            existing_reason = providers_failed.get(p)
            mismatch_reason = f"currency_mismatch_skipped:{count}"
            providers_failed[p] = f"{existing_reason};{mismatch_reason}" if existing_reason else mismatch_reason

        return DiscoveryResult(
            requested_provider=provider_filter,
            client_id=client_id,
            discovered=discovered,
            created=created,
            updated=updated,
            skipped=skipped,
            providers_attempted=providers,
            providers_failed=providers_failed,
            items=items,
        )

    @staticmethod
    def to_response(result: DiscoveryResult) -> AdAccountDiscoverResponse:
        return AdAccountDiscoverResponse(
            requested_provider=result.requested_provider,
            client_id=result.client_id,
            discovered=result.discovered,
            created=result.created,
            updated=result.updated,
            skipped=result.skipped,
            providers_attempted=result.providers_attempted,
            providers_failed=result.providers_failed,
            items=result.items,
        )


