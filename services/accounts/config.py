"""Server-only accounts configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    secret: str = field(repr=False)
    public_origin: str
    issuer: str
    oidc_domain: str
    client_id: str
    client_secret: str = field(default="", repr=False)
    mail_from: str = ""
    aws_region: str = "us-east-1"
    session_seconds: int = 43200
    idle_seconds: int = 1800
    flow_seconds: int = 600
    invitation_seconds: int = 604800

    def __post_init__(self) -> None:
        if not self.database_url or len(self.secret) < 32 or not self.client_id:
            raise ValueError("Accounts require a database URL, a 32-character secret and OIDC client ID")
        for name in ("public_origin", "issuer", "oidc_domain"):
            parsed = urlsplit(getattr(self, name))
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(f"Accounts {name} must be a configured HTTPS URL")
        if urlsplit(self.public_origin).path not in ("", "/"):
            raise ValueError("Accounts public_origin must not contain a path")
        for name in ("session_seconds", "idle_seconds", "flow_seconds", "invitation_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"Accounts {name} must be positive")

    @property
    def callback_url(self) -> str:
        return self.public_origin.rstrip("/") + "/auth/callback"

    @classmethod
    def from_env(cls) -> Settings:
        from shared.db import load_env

        load_env()
        required = ("DATABASE_URL", "SECRET", "PUBLIC_ORIGIN", "OIDC_ISSUER", "OIDC_DOMAIN", "OIDC_CLIENT_ID")
        missing = [name for name in required if not os.environ.get("ACCOUNTS_" + name)]
        if missing:
            raise ValueError("Missing accounts settings: " + ", ".join("ACCOUNTS_" + name for name in missing))
        return cls(
            database_url=os.environ["ACCOUNTS_DATABASE_URL"],
            secret=os.environ["ACCOUNTS_SECRET"],
            public_origin=os.environ["ACCOUNTS_PUBLIC_ORIGIN"].rstrip("/"),
            issuer=os.environ["ACCOUNTS_OIDC_ISSUER"].rstrip("/"),
            oidc_domain=os.environ["ACCOUNTS_OIDC_DOMAIN"].rstrip("/"),
            client_id=os.environ["ACCOUNTS_OIDC_CLIENT_ID"],
            client_secret=os.environ.get("ACCOUNTS_OIDC_CLIENT_SECRET", ""),
            mail_from=os.environ.get("ACCOUNTS_MAIL_FROM", ""),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        )
