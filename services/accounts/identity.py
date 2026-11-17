"""OpenID Connect authorization-code client (Cognito or any standard provider).

Identity claims never grant app roles.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from services.accounts.config import Settings
from services.accounts.security import AccountError


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    email: str
    name: str


class OIDC:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    def authorize_url(self, state: str, nonce: str, verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "response_type": "code", "client_id": self.settings.client_id,
            "redirect_uri": self.settings.callback_url, "scope": "openid email profile",
            "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
        }
        return self.settings.authorization_endpoint + "?" + urlencode(params)

    async def exchange(self, code: str, verifier: str, nonce: str) -> Identity:
        auth = httpx.BasicAuth(self.settings.client_id, self.settings.client_secret) if self.settings.client_secret else None
        try:
            response = await self.client.post(
                self.settings.token_endpoint,
                data={"grant_type": "authorization_code", "client_id": self.settings.client_id,
                      "code": code, "redirect_uri": self.settings.callback_url, "code_verifier": verifier},
                auth=auth,
            )
            response.raise_for_status()
            raw = response.json()["id_token"]
            header = jwt.get_unverified_header(raw)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError("Unexpected signing algorithm or key")
            keys = await self.client.get(self.settings.jwks_endpoint)
            keys.raise_for_status()
            candidates = [key for key in keys.json()["keys"] if key.get("kid") == header["kid"]]
            if len(candidates) != 1:
                raise ValueError("Unknown signing key")
            signing_key = jwt.PyJWK.from_dict(candidates[0], algorithm="RS256").key
            claims = jwt.decode(
                raw, signing_key, algorithms=["RS256"], audience=self.settings.client_id,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
            )
            # Cognito marks its tokens with token_use; a provider that does not set it is checked by audience and nonce.
            if claims.get("nonce") != nonce or claims.get("token_use", "id") != "id" or claims.get("email_verified") is not True:
                raise ValueError("Unverified identity")
            subject, email = claims["sub"], claims.get("email")
            if not isinstance(subject, str) or not subject or not isinstance(email, str) or len(email) > 254 or email.count("@") != 1:
                raise ValueError("Missing subject or email")
            name = claims.get("name", "")
            return Identity(self.settings.issuer, subject, email.casefold(), name[:200] if isinstance(name, str) else "")
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise AccountError(401, "invalid_identity", "Sign-in could not be verified. Please try again.") from exc
        except httpx.HTTPError as exc:
            raise AccountError(503, "identity_unavailable", "Sign-in is temporarily unavailable.") from exc


def discovery_checks(settings: Settings, document: dict) -> list[tuple[str, bool, str]]:
    """Compare the configured endpoints with the provider's discovery document."""
    def same(name: str, configured: str, published) -> tuple[str, bool, str]:
        return name, configured == published, f"configured {configured}, provider publishes {published}"

    algorithms = document.get("id_token_signing_alg_values_supported") or []
    challenges = document.get("code_challenge_methods_supported")
    methods = document.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    checks = [
        same("issuer", settings.issuer, document.get("issuer")),
        same("authorization endpoint", settings.authorization_endpoint, document.get("authorization_endpoint")),
        same("token endpoint", settings.token_endpoint, document.get("token_endpoint")),
        same("signing keys", settings.jwks_endpoint, document.get("jwks_uri")),
        ("RS256 ID tokens", "RS256" in algorithms, "provider signs ID tokens with " + (", ".join(algorithms) or "nothing listed")),
        ("PKCE S256", challenges is None or "S256" in challenges, "provider lists " + (", ".join(challenges) if challenges else "no PKCE methods")),
    ]
    if settings.client_secret:
        checks.append(("client secret over HTTP Basic", "client_secret_basic" in methods, "provider accepts " + ", ".join(methods)))
    return checks
