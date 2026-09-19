"""Optional server-only ID pilot admission, independent from My authorization.

Call only after OIDC verifies the identity. A pilot admission grants no product
permission and never links accounts by email. Absent configuration preserves the
existing ID behavior; a present but invalid configuration always fails closed.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
import unicodedata

from app.services.envidicy_oidc import ISSUER


PILOT_ENV = "ENVIDICY_ID_PILOT_SUBJECTS"
MAX_PILOT_SUBJECTS = 100
MAX_SUBJECT_CHARACTERS = 512
MAX_CONFIGURATION_BYTES = 32 * 1024


class PilotConfigurationError(ValueError):
    """Configuration failure containing no raw configuration or identity data."""

    code = "envidicy_pilot_configuration_invalid"


class PilotSubjectDenied(PermissionError):
    """The verified identity is not admitted to the configured ID pilot."""

    code = "envidicy_pilot_subject_denied"


def _invalid_configuration() -> None:
    raise PilotConfigurationError("Envidicy ID pilot configuration is invalid")


def validate_pilot_subjects(raw: str) -> frozenset[str]:
    """Parse a bounded, nonempty JSON array of unique exact subject strings."""
    if not isinstance(raw, str) or len(raw) > MAX_CONFIGURATION_BYTES:
        _invalid_configuration()
    try:
        if len(raw.encode("utf-8")) > MAX_CONFIGURATION_BYTES:
            _invalid_configuration()
        values = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        # JSONDecodeError contains the source document. Do not chain it into
        # diagnostics, which could otherwise disclose the private pilot list.
        raise PilotConfigurationError("Envidicy ID pilot configuration is invalid") from None
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_PILOT_SUBJECTS:
        _invalid_configuration()
    subjects: set[str] = set()
    for value in values:
        if (not isinstance(value, str) or not 1 <= len(value) <= MAX_SUBJECT_CHARACTERS
                or any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)
                or value in subjects):
            _invalid_configuration()
        # No trimming, case folding, Unicode normalization or email lookup.
        subjects.add(value)
    return frozenset(subjects)


def load_pilot_subjects(
    environment: Mapping[str, str] | None = None, *, enabled: bool = True,
) -> frozenset[str] | None:
    """None means unrestricted; an explicitly empty setting never means None.

    Passing the already-parsed ID rollout flag as enabled=False bypasses even
    reading the setting. Disabled ID configuration cannot break legacy login.
    """
    if not enabled:
        return None
    environment = os.environ if environment is None else environment
    if PILOT_ENV not in environment:
        return None
    return validate_pilot_subjects(environment[PILOT_ENV])


def require_pilot_subject(issuer: str, subject: str, *, subjects: frozenset[str] | None) -> None:
    """Enforce namespace and admission for an already verified OIDC principal.

    Without a pilot list, retain the existing identity_key subject contract
    (including whitespace); configured list members have stricter validation.
    This helper never verifies a JWT or grants My/product capabilities.
    """
    if (issuer != ISSUER or not isinstance(subject, str) or not 1 <= len(subject) <= MAX_SUBJECT_CHARACTERS
            or any(ord(char) < 32 for char in subject)
            or (subjects is not None and subject not in subjects)):
        raise PilotSubjectDenied("Envidicy ID pilot access is not available")
