from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Protocol
from uuid import UUID

from fastapi import HTTPException

from app.schemas import AdAccountCreate, AdAccountDiscoverResponse, AdAccountOut, AdAccountPatch
from app.services.ad_accounts import AdAccountStore
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
        discoverers: Optional[Dict[str, AccountDiscoverer]] = None,
        credential_resolver: Optional[Callable[[str, UUID, Optional[UUID]], Optional[Dict[str, object]]]] = None,
    ):
        self.account_store = account_store
        self.credential_resolver = credential_resolver
        self.discoverers: Dict[str, AccountDiscoverer] = discoverers or {
            "meta": self._discover_meta_accounts,
            "google": self._discover_google_accounts,
            "tiktok": self._discover_tiktok_accounts,
        }

    @staticmethod
    def _discover_meta_accounts(credentials: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
        try:
            rows = meta.list_accounts(credentials)
            if rows:
                return rows
        except Exception:
            pass
        return _fallback_meta_accounts()

    @staticmethod
    def _discover_google_accounts(credentials: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
        try:
            rows = google_ads.list_accounts(credentials)
            if rows:
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
            if rows:
                return rows
        except Exception:
            pass
        return _fallback_tiktok_accounts()

    @staticmethod
    def _safe_provider_error(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                return str(detail.get("message") or detail.get("code") or "Provider discovery failed")
            return str(detail)
        return str(exc) or "Provider discovery failed"

    def discover(
        self,
        *,
        provider: Optional[str],
        client_id: UUID,
        user_id: Optional[UUID] = None,
        upsert_existing: bool = True,
    ) -> DiscoveryResult:
        provider_filter = _normalize_provider(provider)
        if provider_filter == "all":
            providers = [p for p in ("meta", "google", "tiktok") if p in self.discoverers]
        else:
            if provider_filter not in self.discoverers:
                raise HTTPException(status_code=400, detail="Unsupported provider for discovery")
            providers = [provider_filter]

        existing = {
            (
                (a.platform or "").lower().strip(),
                _canonical_external_id((a.platform or "").lower().strip(), a.external_account_id),
            ): a
            for a in self.account_store.list(status="all")
        }
        now_iso = datetime.utcnow().isoformat()

        created = 0
        updated = 0
        skipped = 0
        discovered = 0
        items: List[AdAccountOut] = []
        providers_failed: Dict[str, str] = {}
        provider_conflicts: Dict[str, int] = {}

        for p in providers:
            discoverer = self.discoverers.get(p)
            if not discoverer:
                providers_failed[p] = "Provider discovery not configured"
                continue
            provider_credentials: Optional[Dict[str, object]] = None
            if self.credential_resolver:
                provider_credentials = self.credential_resolver(p, client_id, user_id)
            try:
                try:
                    rows = discoverer(provider_credentials) or []
                except TypeError:
                    # Backward-compatible path for tests/custom discoverers without credentials param.
                    rows = discoverer() or []
            except Exception as exc:
                providers_failed[p] = self._safe_provider_error(exc)
                continue

            for row in rows:
                external_account_id = _canonical_external_id(p, row.get("external_account_id"))
                if not external_account_id:
                    skipped += 1
                    continue
                discovered += 1
                name = str(row.get("name") or f"{p.upper()} {external_account_id}").strip()
                currency = str(row.get("currency") or "USD").strip().upper() or "USD"
                key = (p, external_account_id)
                existing_account = existing.get(key)
                discovery_meta = {
                    "discovered_at": now_iso,
                    "discovery_provider": p,
                    "discovery_source": str(row.get("source") or "provider_api_or_env_fallback"),
                }
                if existing_account:
                    merged_meta = dict(existing_account.metadata or {})
                    merged_meta.update(discovery_meta)
                    if not upsert_existing:
                        skipped += 1
                        items.append(existing_account)
                        continue
                    patch_data: Dict[str, object] = {"metadata": merged_meta}
                    if existing_account.client_id != client_id:
                        patch_data["client_id"] = client_id
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
                    items.append(created_row)
                    created += 1
                except HTTPException as exc:
                    # Conflict-safe upsert fallback: re-read matching account and patch instead of failing discover.
                    if exc.status_code != 409:
                        raise
                    fallback_existing = existing.get(key)
                    if not fallback_existing:
                        refreshed = self.account_store.list(status="all")
                        fallback_existing = next(
                            (
                                a
                                for a in refreshed
                                if (a.platform or "").lower().strip() == p
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
                            client_id=client_id if fallback_existing.client_id != client_id else None,
                            name=name if name and name != fallback_existing.name else None,
                            currency=currency if currency and currency != fallback_existing.currency else None,
                            status="active" if fallback_existing.status != "active" else None,
                            metadata=merged_meta,
                        ),
                    )
                    existing[key] = patched
                    items.append(patched)
                    updated += 1
                except HTTPException as exc:
                    if exc.status_code == 409:
                        skipped += 1
                        provider_conflicts[p] = provider_conflicts.get(p, 0) + 1
                        continue
                    raise
                
                
                
            # Refresh map after provider batch to absorb possible concurrent updates and prevent stale-key conflicts.
            existing = {
                (
                    (a.platform or "").lower().strip(),
                    _canonical_external_id((a.platform or "").lower().strip(), a.external_account_id),
                ): a
                for a in self.account_store.list(status="all")
            }

        for p, count in provider_conflicts.items():
            providers_failed[p] = f"conflict_skipped:{count}"

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
