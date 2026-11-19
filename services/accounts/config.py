"""Server-only accounts configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

MAIL_TRANSPORTS = ("ses", "smtp")


def optional(name: str, default: str = "") -> str:
    return os.environ.get("ACCOUNTS_" + name) or default


def https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not (
        parsed.username or parsed.password or parsed.query or parsed.fragment
    )


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    secret: str = field(repr=False)
    public_origin: str
    # Kept exactly as the provider publishes it: ID tokens must match it character for character.
    issuer: str
    client_id: str
    # Cognito: the hosted UI domain; its /oauth2/* endpoints are the defaults below.
    oidc_domain: str = ""
    # Any other OpenID Connect provider: copy these from its discovery document.
    authorization_url: str = ""
    token_url: str = ""
    jwks_url: str = ""
    client_secret: str = field(default="", repr=False)
    mail_from: str = ""
    mail_transport: str = "ses"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    aws_region: str = "us-east-1"
    session_seconds: int = 43200
    idle_seconds: int = 1800
    flow_seconds: int = 600
    invitation_seconds: int = 604800

    def __post_init__(self) -> None:
        if not self.database_url or len(self.secret) < 32 or not self.client_id:
            raise ValueError("Accounts require a database URL, a 32-character secret and OIDC client ID")
        for name in ("public_origin", "issuer"):
            if not https_url(getattr(self, name)):
                raise ValueError(f"Accounts {name} must be a configured HTTPS URL")
        if urlsplit(self.public_origin).path not in ("", "/"):
            raise ValueError("Accounts public_origin must not contain a path")
        if not self.oidc_domain and not (self.authorization_url and self.token_url):
            raise ValueError("Set ACCOUNTS_OIDC_DOMAIN (Cognito), or ACCOUNTS_OIDC_AUTHORIZATION_URL and ACCOUNTS_OIDC_TOKEN_URL")
        if self.oidc_domain and not https_url(self.oidc_domain):
            raise ValueError("Accounts oidc_domain must be a configured HTTPS URL")
        for name in ("authorization_endpoint", "token_endpoint", "jwks_endpoint"):
            if not https_url(getattr(self, name)):
                raise ValueError(f"Accounts {name} must be an HTTPS URL without a query")
        if self.mail_transport not in MAIL_TRANSPORTS:
            raise ValueError("Accounts mail_transport must be one of: " + ", ".join(MAIL_TRANSPORTS))
        if self.mail_transport == "smtp":
            if not self.smtp_host or not 0 < self.smtp_port < 65536:
                raise ValueError("SMTP mail requires ACCOUNTS_SMTP_HOST and a valid ACCOUNTS_SMTP_PORT")
            if bool(self.smtp_username) != bool(self.smtp_password):
                raise ValueError("Set both ACCOUNTS_SMTP_USERNAME and ACCOUNTS_SMTP_PASSWORD, or neither")
        for name in ("session_seconds", "idle_seconds", "flow_seconds", "invitation_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"Accounts {name} must be positive")

    @property
    def callback_url(self) -> str:
        return self.public_origin.rstrip("/") + "/auth/callback"

    @property
    def authorization_endpoint(self) -> str:
        return self.authorization_url or self.oidc_domain.rstrip("/") + "/oauth2/authorize"

    @property
    def token_endpoint(self) -> str:
        return self.token_url or self.oidc_domain.rstrip("/") + "/oauth2/token"

    @property
    def jwks_endpoint(self) -> str:
        return self.jwks_url or self.issuer.rstrip("/") + "/.well-known/jwks.json"

    @classmethod
    def from_env(cls) -> Settings:
        from shared.db import load_env

        load_env()
        required = ("DATABASE_URL", "SECRET", "PUBLIC_ORIGIN", "OIDC_ISSUER", "OIDC_CLIENT_ID")
        missing = [name for name in required if not os.environ.get("ACCOUNTS_" + name)]
        if missing:
            raise ValueError("Missing accounts settings: " + ", ".join("ACCOUNTS_" + name for name in missing))
        return cls(
            database_url=os.environ["ACCOUNTS_DATABASE_URL"],
            secret=os.environ["ACCOUNTS_SECRET"],
            public_origin=os.environ["ACCOUNTS_PUBLIC_ORIGIN"].rstrip("/"),
            issuer=os.environ["ACCOUNTS_OIDC_ISSUER"],
            client_id=os.environ["ACCOUNTS_OIDC_CLIENT_ID"],
            oidc_domain=optional("OIDC_DOMAIN").rstrip("/"),
            authorization_url=optional("OIDC_AUTHORIZATION_URL"),
            token_url=optional("OIDC_TOKEN_URL"),
            jwks_url=optional("OIDC_JWKS_URL"),
            client_secret=optional("OIDC_CLIENT_SECRET"),
            mail_from=optional("MAIL_FROM"),
            mail_transport=optional("MAIL_TRANSPORT", "ses"),
            smtp_host=optional("SMTP_HOST"),
            smtp_port=int(optional("SMTP_PORT", "587")),
            smtp_username=optional("SMTP_USERNAME"),
            smtp_password=optional("SMTP_PASSWORD"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        )
