"""Verify real RSA-signed ID tokens against mocked OIDC HTTP responses (no database)."""

import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from services.accounts.config import Settings
from services.accounts.identity import OIDC, discovery_checks
from services.accounts.security import AccountError

ABSENT = object()
BASE = {"database_url": "postgresql://unused", "secret": "test-only-" + "x" * 32,
        "public_origin": "https://accounts.test", "client_id": "client-test"}


@pytest.fixture(scope="module")
def settings():
    """Cognito: endpoints derive from the hosted UI domain and the issuer."""
    return Settings(**BASE, issuer="https://identity.test/pool", oidc_domain="https://login.test")


@pytest.fixture(scope="module")
def standard():
    """Another provider: endpoints copied from its discovery document; the issuer ends in a slash."""
    return Settings(**BASE, issuer="https://tenant.provider.test/",
                    authorization_url="https://tenant.provider.test/authorize",
                    token_url="https://tenant.provider.test/oauth/token",
                    jwks_url="https://tenant.provider.test/keys")


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def read_identity(settings, key, changes=None, signing_key=None, algorithm="RS256"):
    claims = {
        "iss": settings.issuer, "aud": settings.client_id, "sub": "subject-1",
        "email": "person@example.org", "email_verified": True,
        "nonce": "nonce-1", "iat": int(time.time()), "exp": int(time.time()) + 60,
        "token_use": "id", "role": "admin",
    }
    claims.update(changes or {})
    claims = {name: value for name, value in claims.items() if value is not ABSENT}
    raw = jwt.encode(claims, signing_key or key, algorithm=algorithm, headers={"kid": "key-1"})
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="key-1", alg="RS256", use="sig")
    requests = []
    def transport(request):
        requests.append(request)
        if str(request.url) == settings.token_endpoint:
            return httpx.Response(200, json={"id_token": raw})
        return httpx.Response(200, json={"keys": [jwk]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await OIDC(settings, client).exchange("code-1", "verifier-1", "nonce-1")
    return asyncio.run(run()), requests


def test_verified_id_token_returns_identity_not_app_role(settings, key):
    identity, requests = read_identity(settings, key)
    assert identity.subject == "subject-1"
    assert identity.email == "person@example.org"
    assert not hasattr(identity, "role")
    assert b"code_verifier=verifier-1" in requests[0].content


@pytest.mark.parametrize("changes", [
    {"iss": "https://attacker.test"}, {"aud": "wrong-client"},
    {"nonce": "wrong-nonce"}, {"token_use": "access"},
    {"email_verified": False}, {"email_verified": "true"},
    {"exp": 1}, {"sub": ""}, {"email": None},
])
def test_invalid_claims_rejected(settings, key, changes):
    with pytest.raises(AccountError) as result:
        read_identity(settings, key, changes)
    assert result.value.status == 401


def test_invalid_signature_rejected(settings, key):
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AccountError) as result:
        read_identity(settings, key, signing_key=impostor)
    assert result.value.status == 401


def test_hmac_algorithm_cannot_replace_rsa(settings, key):
    with pytest.raises(AccountError):
        read_identity(settings, key, signing_key="attacker-secret-that-is-at-least-32-bytes", algorithm="HS256")


def test_cognito_endpoints_derive_from_domain_and_issuer(settings):
    assert settings.authorization_endpoint == "https://login.test/oauth2/authorize"
    assert settings.token_endpoint == "https://login.test/oauth2/token"
    assert settings.jwks_endpoint == "https://identity.test/pool/.well-known/jwks.json"


def test_standard_provider_uses_configured_endpoints_and_exact_issuer(standard, key):
    # Only Cognito sets token_use; other providers' ID tokens leave it out.
    identity, requests = read_identity(standard, key, {"token_use": ABSENT})
    assert [str(request.url) for request in requests] == ["https://tenant.provider.test/oauth/token", "https://tenant.provider.test/keys"]
    assert identity.issuer == "https://tenant.provider.test/"
    assert OIDC(standard, None).authorize_url("s", "n", "v").startswith("https://tenant.provider.test/authorize?")


@pytest.mark.parametrize("changes", [
    {"iss": "https://tenant.provider.test"}, {"token_use": "access"}, {"nonce": "wrong-nonce"},
    {"email_verified": ABSENT}, {"nonce": ABSENT}, {"aud": "another-client"},
])
def test_standard_provider_still_rejects_wrong_claims(standard, key, changes):
    with pytest.raises(AccountError) as result:
        read_identity(standard, key, changes)
    assert result.value.status == 401


@pytest.mark.parametrize("changes", [
    {"oidc_domain": ""},
    {"oidc_domain": "", "authorization_url": "https://login.test/authorize"},
    {"oidc_domain": "http://login.test"},
    {"token_url": "http://login.test/token"},
    {"authorization_url": "https://login.test/authorize?prompt=login"},
    {"jwks_url": "https://user:secret@login.test/keys"},
    {"issuer": "http://identity.test/pool"},
])
def test_settings_reject_missing_or_unsafe_endpoints(changes):
    values = {**BASE, "issuer": "https://identity.test/pool", "oidc_domain": "https://login.test", **changes}
    with pytest.raises(ValueError):
        Settings(**values)


def test_discovery_checks_pass_for_a_matching_document(standard):
    document = {
        "issuer": "https://tenant.provider.test/", "authorization_endpoint": "https://tenant.provider.test/authorize",
        "token_endpoint": "https://tenant.provider.test/oauth/token", "jwks_uri": "https://tenant.provider.test/keys",
        "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["plain", "S256"],
    }
    assert all(passed for _, passed, _ in discovery_checks(standard, document))


def test_discovery_checks_catch_a_dropped_issuer_slash_and_missing_rs256(standard):
    document = {
        "issuer": "https://tenant.provider.test/", "authorization_endpoint": "https://tenant.provider.test/authorize",
        "token_endpoint": "https://tenant.provider.test/oauth/token", "jwks_uri": "https://tenant.provider.test/keys",
        "id_token_signing_alg_values_supported": ["ES256"],
    }
    trimmed = Settings(**{**BASE, "issuer": "https://tenant.provider.test", "authorization_url": standard.authorization_url,
                          "token_url": standard.token_url, "jwks_url": standard.jwks_url, "client_secret": "secret"})
    failed = {name for name, passed, _ in discovery_checks(trimmed, document) if not passed}
    assert failed == {"issuer", "RS256 ID tokens"}
