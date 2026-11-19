"""SMTP delivery and mail settings, against a fake SMTP client (no network, no database)."""

import os
import smtplib
import ssl
from typing import ClassVar

import pytest

from services.accounts import mail
from services.accounts.config import Settings
from services.accounts.mail import Mailer, MailUnavailable

BASE = {
    "database_url": "postgresql://unused", "secret": "test-only-" + "x" * 32, "public_origin": "https://accounts.test",
    "issuer": "https://identity.test/pool", "oidc_domain": "https://login.test", "client_id": "client-test",
    "mail_from": "Wildfire Platform <no-reply@accounts.test>", "mail_transport": "smtp",
    "smtp_host": "smtp.provider.test", "smtp_username": "smtp-user", "smtp_password": "smtp-password-secret",
}


class FakeSMTP:
    calls: ClassVar[list] = []
    sent: ClassVar[list] = []
    fail_on: str | None = None
    tls_offered = True

    def __init__(self, host, port, timeout=None, context=None):
        self.secure = context is not None
        self.record("connect", host, port, timeout, context)

    def record(self, name, *details):
        FakeSMTP.calls.append((name, *details))
        if FakeSMTP.fail_on == name:
            raise smtplib.SMTPResponseException(554, b"rejected")

    def starttls(self, context=None):
        if not FakeSMTP.tls_offered:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        self.record("starttls", context)
        self.secure = True

    def login(self, user, password):
        assert self.secure, "credentials must never cross an unencrypted connection"
        self.record("login", user)

    def send_message(self, message):
        assert self.secure
        self.record("send", message["To"])
        FakeSMTP.sent.append(message)

    def quit(self):
        self.record("quit")

    def close(self):
        FakeSMTP.calls.append(("close",))


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.calls, FakeSMTP.sent, FakeSMTP.fail_on, FakeSMTP.tls_offered = [], [], None, True
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def names():
    return [call[0] for call in FakeSMTP.calls]


def test_starttls_comes_before_login_and_verifies_the_certificate():
    Mailer(Settings(**BASE)).invite("person@example.org", "invite-token")
    assert names() == ["connect", "starttls", "login", "send", "quit", "close"]
    connect, starttls = FakeSMTP.calls[0], FakeSMTP.calls[1]
    assert connect[1:4] == ("smtp.provider.test", 587, 10)
    context = starttls[1]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    message = FakeSMTP.sent[0]
    assert message["From"] == "Wildfire Platform <no-reply@accounts.test>"
    assert message["Subject"] == "Your Wildfire Platform invitation"
    assert "https://accounts.test/invite#token=invite-token" in message.get_content()


def test_port_465_uses_implicit_tls():
    Mailer(Settings(**{**BASE, "smtp_port": 465})).review("person@example.org", "approved", "Welcome")
    assert names() == ["connect", "login", "send", "quit", "close"]
    context = FakeSMTP.calls[0][4]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_server_without_starttls_gets_no_credentials_and_no_message():
    FakeSMTP.tls_offered = False
    with pytest.raises(MailUnavailable) as failure:
        Mailer(Settings(**BASE)).invite("person@example.org", "invite-token")
    assert str(failure.value) == "mail_delivery_failed"
    assert "login" not in names() and "send" not in names()


@pytest.mark.parametrize("step", ["connect", "login", "send"])
def test_delivery_failures_are_one_stable_code_without_secrets(step):
    FakeSMTP.fail_on = step
    with pytest.raises(MailUnavailable) as failure:
        Mailer(Settings(**BASE)).invite("person@example.org", "invite-token")
    assert str(failure.value) == "mail_delivery_failed"
    assert "smtp-password-secret" not in repr(failure.value) and "invite-token" not in repr(failure.value)


def test_failed_goodbye_after_delivery_is_not_a_failure():
    FakeSMTP.fail_on = "quit"
    Mailer(Settings(**BASE)).invite("person@example.org", "invite-token")
    assert len(FakeSMTP.sent) == 1


def test_relay_without_credentials_skips_login():
    Mailer(Settings(**{**BASE, "smtp_username": "", "smtp_password": ""})).invite("person@example.org", "invite-token")
    assert names() == ["connect", "starttls", "send", "quit", "close"]


def test_address_with_a_line_break_is_refused_before_sending():
    with pytest.raises(MailUnavailable):
        Mailer(Settings(**BASE)).invite("person@example.org\nBcc: other@example.org", "invite-token")
    assert not FakeSMTP.sent


def test_missing_sender_sends_nothing():
    with pytest.raises(MailUnavailable) as failure:
        Mailer(Settings(**{**BASE, "mail_from": ""})).invite("person@example.org", "invite-token")
    assert str(failure.value) == "mail_not_configured"
    assert not FakeSMTP.calls


@pytest.mark.parametrize("changes", [
    {"mail_transport": "sendmail"},
    {"smtp_host": ""},
    {"smtp_port": 0},
    {"smtp_password": ""},
    {"smtp_username": ""},
])
def test_settings_reject_incomplete_smtp(changes):
    with pytest.raises(ValueError):
        Settings(**{**BASE, **changes})


def test_settings_from_env_read_smtp_and_keep_the_issuer_exact(monkeypatch):
    monkeypatch.setattr("shared.db.load_env", lambda **_: None)
    for name in list(os.environ):
        if name.startswith("ACCOUNTS_"):
            monkeypatch.delenv(name)
    values = {
        "DATABASE_URL": "postgresql://unused", "SECRET": "test-only-" + "x" * 32, "PUBLIC_ORIGIN": "https://accounts.test/",
        "OIDC_ISSUER": "https://tenant.provider.test/", "OIDC_CLIENT_ID": "client-test",
        "OIDC_AUTHORIZATION_URL": "https://tenant.provider.test/authorize", "OIDC_TOKEN_URL": "https://tenant.provider.test/oauth/token",
        "OIDC_JWKS_URL": "https://tenant.provider.test/keys", "MAIL_FROM": "no-reply@accounts.test", "MAIL_TRANSPORT": "smtp",
        "SMTP_HOST": "smtp.provider.test", "SMTP_PORT": "465", "SMTP_USERNAME": "smtp-user", "SMTP_PASSWORD": "smtp-password-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv("ACCOUNTS_" + name, value)
    settings = Settings.from_env()
    assert settings.issuer == "https://tenant.provider.test/"
    assert settings.public_origin == "https://accounts.test"
    assert (settings.mail_transport, settings.smtp_host, settings.smtp_port) == ("smtp", "smtp.provider.test", 465)
    assert settings.oidc_domain == "" and settings.token_endpoint == "https://tenant.provider.test/oauth/token"
    assert "smtp-password-secret" not in repr(settings)
