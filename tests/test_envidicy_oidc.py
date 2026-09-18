import asyncio
import base64
from dataclasses import FrozenInstanceError
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes
import httpx
import jwt
import pytest

from app.services import envidicy_oidc as oidc


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def config():
    return oidc.EnvidicyOidcConfig(enabled=True, client_secret="test-only-client-secret-" + "x" * 32)


@pytest.fixture
def transaction():
    return oidc.generate_login_transaction()


@pytest.fixture
def claims(transaction):
    now = int(time.time())
    return {"iss": oidc.ISSUER, "sub": "immutable-user-123", "aud": oidc.CLIENT_ID,
            "azp": oidc.CLIENT_ID, "iat": now, "exp": now + 300, "nonce": transaction.nonce,
            "name": "Test Person", "email": "unverified@example.test", "email_verified": False}


@pytest.fixture
def jwks(signing_key):
    key = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    return {"keys": [{**key, "kid": "test-key", "alg": "RS256", "use": "sig", "key_ops": ["verify"]}]}


def encode(claims, signing_key, **headers):
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key", **headers})


def discovery():
    return {"issuer": oidc.ISSUER, "authorization_endpoint": oidc.AUTHORIZATION_ENDPOINT,
            "token_endpoint": oidc.TOKEN_ENDPOINT, "jwks_uri": oidc.JWKS_URI,
            "end_session_endpoint": oidc.END_SESSION_ENDPOINT,
            "id_token_signing_alg_values_supported": ["RS256"], "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic"]}


def run_exchange(config, transaction, handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            return await oidc.exchange_code(config, code="opaque-authorization-code", code_verifier=transaction.code_verifier,
                                            expected_nonce=transaction.nonce, client=client)
    return asyncio.run(run())


def test_disabled_configuration_does_not_read_or_validate_any_secrets(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("secret file must not be opened")
    monkeypatch.setattr(oidc.Path, "open", forbidden)
    config = oidc.EnvidicyOidcConfig.from_env({"ENVIDICY_ID_CLIENT_SECRET": "short", "ENVIDICY_ID_CLIENT_SECRET_FILE": "missing"})
    assert not config.enabled
    assert not config.client_secret
    with pytest.raises(oidc.EnvidicyOidcError, match="disabled"):
        oidc.authorization_url(config, state="", nonce="", code_verifier="")


def test_secret_sources_are_bounded_mutually_exclusive_and_hidden(tmp_path):
    secret = "test-only-client-secret-" + "x" * 32
    file = tmp_path / "secret"
    file.write_text(secret + "\n", encoding="utf-8")
    config = oidc.EnvidicyOidcConfig.from_env({"ENVIDICY_ID_ENABLED": "true", "ENVIDICY_ID_CLIENT_SECRET_FILE": str(file)})
    assert config.client_secret == secret
    assert secret not in repr(config)
    with pytest.raises(oidc.EnvidicyOidcError, match="exactly one"):
        oidc.EnvidicyOidcConfig.from_env({"ENVIDICY_ID_ENABLED": "true", "ENVIDICY_ID_CLIENT_SECRET": secret,
                                        "ENVIDICY_ID_CLIENT_SECRET_FILE": str(file)})
    file.write_bytes(b"x" * 4097)
    with pytest.raises(oidc.EnvidicyOidcError, match="unavailable or invalid"):
        oidc.EnvidicyOidcConfig.from_env({"ENVIDICY_ID_ENABLED": "true", "ENVIDICY_ID_CLIENT_SECRET_FILE": str(file)})


@pytest.mark.parametrize("origin", ["https://dash.envidicy.com", "https://evil.example", "https://dash.envidicy.kz.evil.example",
                                    "https://dash.envidicy.kz/path", "http://127.0.0.1:3000"])
def test_origin_cannot_be_retargeted(config, origin):
    with pytest.raises(oidc.EnvidicyOidcError, match="origin"):
        oidc.EnvidicyOidcConfig(enabled=True, client_secret=config.client_secret, public_origin=origin)


def test_loopback_requires_explicit_constructor_opt_in(config):
    local = oidc.EnvidicyOidcConfig(enabled=True, client_secret=config.client_secret,
                                    public_origin="http://127.0.0.1:3000", allow_loopback=True)
    assert local.redirect_uri == "http://127.0.0.1:3000/api/backend/auth/envidicy/callback"
    assert local.issuer == oidc.ISSUER


def test_authorization_and_logout_use_exact_routes_and_unique_pkce(config, transaction):
    other = oidc.generate_login_transaction()
    assert len({transaction.state, transaction.nonce, transaction.code_verifier, other.state, other.nonce, other.code_verifier}) == 6
    query = parse_qs(urlsplit(oidc.authorization_url(config, state=transaction.state, nonce=transaction.nonce,
                                                   code_verifier=transaction.code_verifier)).query)
    expected = base64.urlsafe_b64encode(hashlib.sha256(transaction.code_verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [expected]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://dash.envidicy.kz/api/backend/auth/envidicy/callback"]
    assert query["response_type"] == ["code"]
    assert query["nonce"] == [transaction.nonce]
    logout = parse_qs(urlsplit(oidc.logout_url(config, state=transaction.state)).query)
    assert logout["post_logout_redirect_uri"] == ["https://dash.envidicy.kz/api/backend/auth/envidicy/logout/callback"]
    assert logout["client_id"] == [oidc.CLIENT_ID]
    assert "id_token_hint" not in logout
    assert transaction.code_verifier not in repr(transaction)


def test_valid_identity_is_immutable_and_returns_no_tokens(config, transaction, claims, signing_key, jwks):
    token = encode(claims, signing_key)
    identity = oidc.validate_id_token(config, id_token=token, expected_nonce=transaction.nonce, jwks=jwks)
    assert identity.issuer == oidc.ISSUER
    assert identity.subject == "immutable-user-123"
    assert identity.email == "unverified@example.test"  # Profile only; never an identity/authorization key.
    assert set(vars(identity)) == {"issuer", "subject", "name", "email"}
    assert token not in repr(identity)
    with pytest.raises(FrozenInstanceError):
        identity.subject = "changed"


@pytest.mark.parametrize("change", [
    {"iss": "https://evil.example/realms/envidicy"}, {"aud": "envidicy-my"},
    {"aud": [oidc.CLIENT_ID, "untrusted-client"]}, {"azp": "envidicy-my"}, {"nonce": "wrong"},
    {"sub": ""}, {"sub": "bad\nsubject"}, {"sub": 123}, {"exp": 0}, {"exp": "9999999999"},
    {"iat": True}, {"iat": float("inf")}, {"iat": 1}, {"iat": 9999999999}, {"nbf": 9999999999},
])
def test_untrusted_claims_are_rejected(config, transaction, claims, signing_key, jwks, change):
    token = encode({**claims, **change}, signing_key)
    with pytest.raises(oidc.EnvidicyOidcError):
        oidc.validate_id_token(config, id_token=token, expected_nonce=transaction.nonce, jwks=jwks)


@pytest.mark.parametrize("missing", ["iss", "sub", "aud", "exp", "iat", "nonce"])
def test_required_claims_are_not_optional(config, transaction, claims, signing_key, jwks, missing):
    claims.pop(missing)
    with pytest.raises(oidc.EnvidicyOidcError):
        oidc.validate_id_token(config, id_token=encode(claims, signing_key), expected_nonce=transaction.nonce, jwks=jwks)


def test_single_element_audience_and_no_email_are_valid(config, transaction, claims, signing_key, jwks):
    claims["aud"] = [oidc.CLIENT_ID]
    claims.pop("email")
    claims.pop("azp")
    identity = oidc.validate_id_token(config, id_token=encode(claims, signing_key), expected_nonce=transaction.nonce, jwks=jwks)
    assert identity.email is None


@pytest.mark.parametrize("header", [{"kid": "unknown"}, {"jku": "https://evil.example/keys"}, {"crit": []}, {"b64": False}])
def test_jose_header_cannot_select_remote_keys_or_extensions(config, transaction, claims, signing_key, jwks, header):
    token = encode(claims, signing_key, **header)
    with pytest.raises(oidc.EnvidicyOidcError):
        oidc.validate_id_token(config, id_token=token, expected_nonce=transaction.nonce, jwks=jwks)


def test_signature_and_algorithm_are_checked(config, transaction, claims, signing_key, jwks):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for token in [encode(claims, other_key), jwt.encode(claims, "not-an-rsa-key-" * 4, algorithm="HS256", headers={"kid": "test-key"})]:
        with pytest.raises(oidc.EnvidicyOidcError):
            oidc.validate_id_token(config, id_token=token, expected_nonce=transaction.nonce, jwks=jwks)


def test_duplicate_json_claims_are_rejected_even_with_valid_signature(config, transaction, claims, signing_key, jwks):
    def b64(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")
    raw_claims = json.dumps(claims)[:-1] + ', "sub": "another-user"}'
    body = b64(b'{"alg":"RS256","kid":"test-key"}') + b"." + b64(raw_claims.encode())
    signature = signing_key.sign(body, padding.PKCS1v15(), hashes.SHA256())
    token = (body + b"." + b64(signature)).decode()
    with pytest.raises(oidc.EnvidicyOidcError):
        oidc.validate_id_token(config, id_token=token, expected_nonce=transaction.nonce, jwks=jwks)


def test_code_exchange_uses_fixed_endpoints_basic_auth_and_no_tokens_out(config, transaction, claims, signing_key, jwks, caplog):
    token = encode(claims, signing_key)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == oidc.DISCOVERY_URL:
            return httpx.Response(200, json=discovery())
        if str(request.url) == oidc.TOKEN_ENDPOINT:
            assert request.method == "POST"
            assert request.headers["authorization"].startswith("Basic ")
            body = parse_qs(request.content.decode())
            assert body["code_verifier"] == [transaction.code_verifier]
            assert body["redirect_uri"] == [config.redirect_uri]
            assert "client_secret" not in body
            return httpx.Response(200, json={"id_token": token, "access_token": "never-return-this", "refresh_token": "or-this"})
        assert str(request.url) == oidc.JWKS_URI
        return httpx.Response(200, json=jwks)
    identity = run_exchange(config, transaction, handler)
    assert identity.subject == claims["sub"]
    assert calls == [oidc.DISCOVERY_URL, oidc.TOKEN_ENDPOINT, oidc.JWKS_URI]
    assert token not in caplog.text
    assert config.client_secret not in caplog.text


@pytest.mark.parametrize("endpoint", ["issuer", "token_endpoint", "jwks_uri", "authorization_endpoint", "end_session_endpoint"])
def test_discovery_cannot_redirect_credentials_or_jwks_to_another_url(config, transaction, endpoint):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={**discovery(), endpoint: "http://169.254.169.254/"})
    with pytest.raises(oidc.EnvidicyOidcError, match="discovery"):
        run_exchange(config, transaction, handler)
    assert calls == [oidc.DISCOVERY_URL]


@pytest.mark.parametrize("status", [301, 302, 307, 400, 500])
def test_redirects_and_provider_errors_are_rejected_without_following_or_leaking(config, transaction, status):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"location": "https://evil.example"}, text="private-provider-error")
    with pytest.raises(oidc.EnvidicyOidcError) as error:
        run_exchange(config, transaction, handler)
    assert calls == [oidc.DISCOVERY_URL]
    assert "private-provider-error" not in str(error.value)


def test_response_size_is_bounded_even_without_content_length(config, transaction):
    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(9):
                yield b" " * 8192
    with pytest.raises(oidc.EnvidicyOidcError, match="too large"):
        run_exchange(config, transaction, lambda _request: httpx.Response(200, headers={"content-type": "application/json"}, stream=Oversized()))


def test_exchange_has_a_total_deadline(config, transaction, monkeypatch):
    monkeypatch.setattr(oidc, "EXCHANGE_TIMEOUT_SECONDS", 0.01)
    async def handler(_request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=discovery())
    with pytest.raises(oidc.EnvidicyOidcError, match="temporarily unavailable"):
        run_exchange(config, transaction, handler)
