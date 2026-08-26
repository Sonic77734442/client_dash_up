from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.services.credential_crypto import (
    CredentialCryptoError,
    CredentialDecryptionError,
    CredentialEncryptionUnavailable,
    CredentialKeyring,
)


AUTH_SECRET_MARKER_FIELD = "__auth_provider_secret_envelope__"
AUTH_SECRET_MARKER = "encrypted-auth-provider-client-secret"
AUTH_SECRET_VERSION = 1
AUTH_SECRET_ALGORITHM = "A256GCM"
_AUTH_SECRET_HKDF_INFO = b"client-dash-up/auth_provider_configs.client_secret/A256GCM/v1"
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: object, *, field: str) -> bytes:
    encoded = str(value or "").strip()
    if not encoded or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise CredentialDecryptionError(f"Encrypted auth provider secret {field} is invalid")
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise CredentialDecryptionError(
            f"Encrypted auth provider secret {field} is invalid"
        ) from exc
    if _b64url_encode(decoded) != encoded:
        raise CredentialDecryptionError(f"Encrypted auth provider secret {field} is invalid")
    return decoded


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _aad(*, config_id: object, provider: object, key_id: object) -> bytes:
    return _canonical_json(
        {
            "context": "auth_provider_configs.client_secret",
            "envelope_version": AUTH_SECRET_VERSION,
            "config_id": str(config_id or "").strip(),
            "provider": str(provider or "").strip().lower(),
            "key_id": str(key_id or "").strip(),
        }
    )


def _derive_auth_key(root_key: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_AUTH_SECRET_HKDF_INFO,
    ).derive(root_key)


def _parsed_envelope(stored: object) -> Optional[Mapping[str, object]]:
    if not isinstance(stored, str):
        raise CredentialDecryptionError("Stored auth provider secret is invalid")
    try:
        parsed = json.loads(stored)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    if parsed.get(AUTH_SECRET_MARKER_FIELD) == AUTH_SECRET_MARKER:
        return parsed
    # A document that advertises any credential envelope is not legacy
    # plaintext. Reject wrong versions/domains instead of using their JSON as
    # an OAuth secret.
    if AUTH_SECRET_MARKER_FIELD in parsed or "__credential_envelope__" in parsed:
        raise CredentialDecryptionError("Stored auth provider secret envelope is unsupported")
    return None


def is_encrypted_auth_provider_secret(stored: object) -> bool:
    try:
        return _parsed_envelope(stored) is not None
    except CredentialCryptoError:
        return False


def auth_provider_secret_key_id(stored: object) -> Optional[str]:
    envelope = _parsed_envelope(stored)
    if envelope is None:
        return None
    key_id = str(envelope.get("kid") or "").strip()
    return key_id or None


@dataclass(frozen=True)
class AuthProviderSecretCipher:
    """A separate HKDF/AAD domain backed by the deployment credential keyring."""

    keyring: CredentialKeyring

    @classmethod
    def from_env(cls) -> "AuthProviderSecretCipher":
        return cls(CredentialKeyring.from_env())

    @property
    def enabled(self) -> bool:
        return self.keyring.enabled

    def encrypt(self, secret: str, *, config_id: object, provider: object) -> str:
        if not self.keyring.enabled or not self.keyring.active_key_id:
            raise CredentialEncryptionUnavailable(
                "Auth provider secret encryption is not configured"
            )
        key_id = self.keyring.active_key_id
        nonce = os.urandom(12)
        ciphertext = AESGCM(_derive_auth_key(self.keyring.keys[key_id])).encrypt(
            nonce,
            str(secret).encode("utf-8"),
            _aad(config_id=config_id, provider=provider, key_id=key_id),
        )
        envelope = {
            AUTH_SECRET_MARKER_FIELD: AUTH_SECRET_MARKER,
            "v": AUTH_SECRET_VERSION,
            "alg": AUTH_SECRET_ALGORITHM,
            "kid": key_id,
            "nonce": _b64url_encode(nonce),
            "ciphertext": _b64url_encode(ciphertext),
        }
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=True)

    def decrypt(self, stored: object, *, config_id: object, provider: object) -> str:
        envelope = _parsed_envelope(stored)
        if envelope is None:
            # Transitional dual-read for rows written before encryption. New
            # persistent writes never take this path.
            return str(stored)
        if envelope.get("v") != AUTH_SECRET_VERSION or envelope.get("alg") != AUTH_SECRET_ALGORITHM:
            raise CredentialDecryptionError(
                "Encrypted auth provider secret envelope version is unsupported"
            )
        key_id = str(envelope.get("kid") or "").strip()
        if not _KEY_ID_PATTERN.fullmatch(key_id):
            raise CredentialDecryptionError("Encrypted auth provider secret key id is invalid")
        root_key = self.keyring.keys.get(key_id)
        if root_key is None:
            raise CredentialEncryptionUnavailable(
                "The key required to decrypt an auth provider secret is unavailable"
            )
        nonce = _b64url_decode(envelope.get("nonce"), field="nonce")
        ciphertext = _b64url_decode(envelope.get("ciphertext"), field="ciphertext")
        if len(nonce) != 12 or len(ciphertext) < 16:
            raise CredentialDecryptionError("Encrypted auth provider secret envelope is invalid")
        try:
            plaintext = AESGCM(_derive_auth_key(root_key)).decrypt(
                nonce,
                ciphertext,
                _aad(config_id=config_id, provider=provider, key_id=key_id),
            )
        except InvalidTag as exc:
            raise CredentialDecryptionError(
                "Encrypted auth provider secret authentication failed"
            ) from exc
        except Exception as exc:
            raise CredentialDecryptionError(
                "Encrypted auth provider secret could not be decrypted"
            ) from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialDecryptionError(
                "Encrypted auth provider secret payload is invalid"
            ) from exc
