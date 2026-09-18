"""Default-off closure of legacy *human* authentication after verified cutover.

This policy does not gate service credentials or provider API calls. Invalid
configuration fails closed; an ID outage must never reopen local authentication.
The rollout switch is independent of ID availability and authorization in My.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import HTTPException


def id_only_enabled(environment: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environment is None else environment
    raw = str(environment.get("ENVIDICY_ID_ONLY_ENABLED", "false")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=503, detail={
        "code": "envidicy_cutover_configuration_invalid",
        "message": "The login policy is unavailable; contact the service operator",
    })


def require_legacy_auth_enabled() -> None:
    if id_only_enabled():
        raise HTTPException(status_code=410, detail={
            "code": "envidicy_id_login_required",
            "message": "Local login is closed; sign in through Envidicy ID",
        })


def enforce_session_origin(session) -> None:
    """Never infer an ID session from its user's newly migrated principal."""
    if id_only_enabled() and session.auth_method != "envidicy_id":
        raise HTTPException(status_code=401, detail={
            "code": "envidicy_id_login_required",
            "message": "This local session is no longer accepted; sign in through Envidicy ID",
        })
