from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ENVELOPE_MARKER = "encrypted-integration-credential"
ENVELOPE_VERSION = 1
ENVELOPE_ALGORITHM = "A256GCM"
KEYRING_ENV = "INTEGRATION_CREDENTIAL_ENCRYPTION_KEYS"
ACTIVE_KEY_ID_ENV = "INTEGRATION_CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID"

_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class CredentialCryptoError(RuntimeError):
    """Base class whose messages are always safe to surface or log."""


class CredentialEncryptionConfigurationError(CredentialCryptoError):
    pass


class CredentialEncryptionUnavailable(CredentialCryptoError):
    pass


class CredentialDecryptionError(CredentialCryptoError):
    pass


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: object, *, field: str) -> bytes:
    encoded = str(value or "").strip()
    if not encoded or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise CredentialDecryptionError(f"Encrypted credential {field} is invalid")
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise CredentialDecryptionError(f"Encrypted credential {field} is invalid") from exc
    # Reject alternate base64 spellings with non-zero unused padding bits.
    # The envelope has one canonical representation, which keeps integrity
    # checks, compare-and-swap rotation, and audit evidence unambiguous.
    if _b64url_encode(decoded) != encoded:
        raise CredentialDecryptionError(f"Encrypted credential {field} is invalid")
    return decoded


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def is_encrypted_credential_envelope(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("__credential_envelope__") == ENVELOPE_MARKER


def credential_envelope_key_id(value: object) -> Optional[str]:
    if not is_encrypted_credential_envelope(value):
        return None
    key_id = str(value.get("kid") or "").strip()  # type: ignore[union-attr]
    return key_id or None


def credential_aad(
    *,
    credential_id: object,
    provider: object,
    scope_type: object,
    scope_id: object,
    connection_key: object,
    key_id: object,
) -> bytes:
    """Bind ciphertext to the immutable server-side identity of its database row."""

    return _canonical_json(
        {
            "context": "integration_credentials.credentials_json",
            "envelope_version": ENVELOPE_VERSION,
            "credential_id": str(credential_id or "").strip(),
            "provider": str(provider or "").strip().lower(),
            "scope_type": str(scope_type or "").strip().lower(),
            "scope_id": str(scope_id).strip() if scope_id is not None else None,
            "connection_key": str(connection_key or "default").strip() or "default",
            "key_id": str(key_id or "").strip(),
        }
    )


@dataclass(frozen=True)
class CredentialKeyring:
    """Versioned AES-256-GCM keyring loaded from deployment secrets.

    ``INTEGRATION_CREDENTIAL_ENCRYPTION_KEYS`` is a JSON object mapping a
    non-secret key id to a URL-safe base64 encoded 32-byte key. The separate
    active key id makes rotation non-disruptive: keep the old key in the map,
    switch the active id, rotate stored envelopes, then remove the old key.

    An entirely absent configuration is intentionally allowed so an existing
    deployment can still boot and read legacy plaintext credentials. New
    persistent credential writes nevertheless fail closed until a key exists.
    """

    keys: Mapping[str, bytes] = field(repr=False)
    active_key_id: Optional[str]

    def __post_init__(self) -> None:
        active_key_id = str(self.active_key_id or "").strip() or None
        normalized: dict[str, bytes] = {}
        for raw_key_id, raw_key in self.keys.items():
            key_id = str(raw_key_id or "").strip()
            if not _KEY_ID_PATTERN.fullmatch(key_id):
                raise CredentialEncryptionConfigurationError(
                    "Integration credential encryption key id is invalid"
                )
            try:
                key = bytes(raw_key)
            except Exception as exc:
                raise CredentialEncryptionConfigurationError(
                    "Integration credential encryption key material is invalid"
                ) from exc
            if len(key) != 32:
                raise CredentialEncryptionConfigurationError(
                    "Integration credential encryption keys must be 32 bytes"
                )
            normalized[key_id] = key
        if not normalized and active_key_id is None:
            object.__setattr__(self, "keys", MappingProxyType({}))
            object.__setattr__(self, "active_key_id", None)
            return
        if not active_key_id or active_key_id not in normalized:
            raise CredentialEncryptionConfigurationError(
                "Integration credential encryption active key is not present in the keyring"
            )
        object.__setattr__(self, "keys", MappingProxyType(normalized))
        object.__setattr__(self, "active_key_id", active_key_id)

    @classmethod
    def disabled(cls) -> "CredentialKeyring":
        return cls(keys={}, active_key_id=None)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "CredentialKeyring":
        values = environ if environ is not None else os.environ
        raw_keys = str(values.get(KEYRING_ENV, "") or "").strip()
        active_key_id = str(values.get(ACTIVE_KEY_ID_ENV, "") or "").strip()
        if not raw_keys and not active_key_id:
            return cls.disabled()
        if not raw_keys or not active_key_id:
            raise CredentialEncryptionConfigurationError(
                "Integration credential encryption keyring configuration is incomplete"
            )
        if not _KEY_ID_PATTERN.fullmatch(active_key_id):
            raise CredentialEncryptionConfigurationError(
                "Integration credential encryption active key id is invalid"
            )
        try:
            parsed = json.loads(raw_keys)
        except Exception as exc:
            raise CredentialEncryptionConfigurationError(
                "Integration credential encryption keyring is invalid"
            ) from exc
        if not isinstance(parsed, dict) or not parsed:
            raise CredentialEncryptionConfigurationError(
                "Integration credential encryption keyring is invalid"
            )

        decoded: dict[str, bytes] = {}
        for raw_key_id, raw_key in parsed.items():
            key_id = str(raw_key_id or "").strip()
            if not _KEY_ID_PATTERN.fullmatch(key_id):
                raise CredentialEncryptionConfigurationError(
                    "Integration credential encryption key id is invalid"
                )
            try:
                key = base64.b64decode(
                    str(raw_key or "").strip() + "=" * (-len(str(raw_key or "").strip()) % 4),
                    altchars=b"-_",
                    validate=True,
                )
            except Exception as exc:
                raise CredentialEncryptionConfigurationError(
                    "Integration credential encryption key material is invalid"
                ) from exc
            if len(key) != 32:
                raise CredentialEncryptionConfigurationError(
                    "Integration credential encryption keys must be 32 bytes"
                )
            decoded[key_id] = key
        if active_key_id not in decoded:
            raise CredentialEncryptionConfigurationError(
                "Integration credential encryption active key is not present in the keyring"
            )
        return cls(keys=decoded, active_key_id=active_key_id)

    @property
    def enabled(self) -> bool:
        return bool(self.active_key_id and self.active_key_id in self.keys)

    def has_key(self, key_id: object) -> bool:
        return str(key_id or "").strip() in self.keys

    def encrypt_json(self, value: Mapping[str, Any], *, aad_context: Mapping[str, object]) -> dict[str, object]:
        if not self.enabled or not self.active_key_id:
            raise CredentialEncryptionUnavailable(
                "Integration credential encryption is not configured"
            )
        if not isinstance(value, Mapping):
            raise CredentialEncryptionUnavailable("Integration credential payload is invalid")
        key_id = self.active_key_id
        nonce = os.urandom(12)
        aad = credential_aad(key_id=key_id, **aad_context)
        plaintext = _canonical_json(dict(value))
        ciphertext = AESGCM(self.keys[key_id]).encrypt(nonce, plaintext, aad)
        return {
            "__credential_envelope__": ENVELOPE_MARKER,
            "v": ENVELOPE_VERSION,
            "alg": ENVELOPE_ALGORITHM,
            "kid": key_id,
            "nonce": _b64url_encode(nonce),
            "ciphertext": _b64url_encode(ciphertext),
        }

    def decrypt_json(self, envelope: object, *, aad_context: Mapping[str, object]) -> dict[str, Any]:
        if not is_encrypted_credential_envelope(envelope):
            raise CredentialDecryptionError("Stored credential is not an encrypted envelope")
        assert isinstance(envelope, Mapping)
        if envelope.get("v") != ENVELOPE_VERSION or envelope.get("alg") != ENVELOPE_ALGORITHM:
            raise CredentialDecryptionError("Encrypted credential envelope version is unsupported")
        key_id = str(envelope.get("kid") or "").strip()
        if not _KEY_ID_PATTERN.fullmatch(key_id):
            raise CredentialDecryptionError("Encrypted credential key id is invalid")
        key = self.keys.get(key_id)
        if key is None:
            raise CredentialEncryptionUnavailable(
                "The key required to decrypt an integration credential is unavailable"
            )
        nonce = _b64url_decode(envelope.get("nonce"), field="nonce")
        ciphertext = _b64url_decode(envelope.get("ciphertext"), field="ciphertext")
        if len(nonce) != 12 or len(ciphertext) < 16:
            raise CredentialDecryptionError("Encrypted credential envelope is invalid")
        aad = credential_aad(key_id=key_id, **aad_context)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise CredentialDecryptionError(
                "Encrypted credential authentication failed"
            ) from exc
        except Exception as exc:
            raise CredentialDecryptionError("Encrypted credential could not be decrypted") from exc
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise CredentialDecryptionError("Encrypted credential payload is invalid") from exc
        if not isinstance(payload, dict):
            raise CredentialDecryptionError("Encrypted credential payload is invalid")
        return payload
