"""Verify real RSA-signed ID tokens against mocked OIDC HTTP responses."""

import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from services.accounts.identity import OIDC
from services.accounts.security import AccountError


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
    raw = jwt.encode(claims, signing_key or key, algorithm=algorithm, headers={"kid": "key-1"})
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="key-1", alg="RS256", use="sig")
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path == "/oauth2/token":
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
