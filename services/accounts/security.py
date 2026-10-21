"""Opaque credentials, encrypted login flows and request validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

SESSION_COOKIE = "__Host-wildfire_session"
FLOW_COOKIE = "__Host-wildfire_flow"
INVITE_COOKIE = "__Host-wildfire_invite"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AccountError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def token() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf(secret: str, session: str) -> str:
    return hmac.new(secret.encode(), ("csrf:" + session).encode(), hashlib.sha256).hexdigest()


def check_write(secret: str, session: str, supplied: str | None, origin: str | None, expected_origin: str) -> None:
    if origin != expected_origin or not supplied or not supplied.isascii() or not hmac.compare_digest(csrf(secret, session), supplied):
        raise AccountError(403, "csrf_failed", "Please refresh the page and try again.")


def return_path(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise AccountError(422, "invalid_return_path", "The return path must stay on this website.") from exc
    if not value.startswith("/") or value.startswith("//") or parsed.netloc or parsed.scheme or "\\" in value or any(ord(c) < 32 for c in value):
        raise AccountError(422, "invalid_return_path", "The return path must stay on this website.")
    if parsed.path not in ("/", "/apply", "/access-status", "/invite", "/admin", "/workspace"):
        raise AccountError(422, "invalid_return_path", "The return path is not supported.")
    if parsed.query or parsed.fragment:
        raise AccountError(422, "invalid_return_path", "Return paths cannot contain query strings or fragments.")
    return value


class FlowCipher:
    def __init__(self, secret: str):
        key = hashlib.sha256(("oidc-flow:" + secret).encode()).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key))

    def seal(self, value: dict) -> str:
        return self.fernet.encrypt(json.dumps(value).encode()).decode()

    def open(self, value: str) -> dict:
        try:
            return json.loads(self.fernet.decrypt(value.encode()).decode())
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountError(401, "invalid_login_flow", "Please start signing in again.") from exc
