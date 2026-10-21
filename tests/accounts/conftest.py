"""Accounts tests run only against an explicitly isolated PostgreSQL database."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import make_conninfo
import pytest
from fastapi.testclient import TestClient

from services.accounts.app import create_app
from services.accounts.cli import grant_runtime
from services.accounts.config import Settings
from services.accounts.identity import Identity
from services.accounts.mail import MailUnavailable
from services.accounts.security import csrf, token
from services.accounts.store import Store


@pytest.fixture(scope="session")
def settings():
    dsn = os.environ.get("ACCOUNTS_TEST_DATABASE_URL", "")
    target = urlsplit(dsn)
    if target.hostname not in {"127.0.0.1", "localhost"} or not target.path.startswith("/accounts_test_"):
        pytest.fail("Set ACCOUNTS_TEST_DATABASE_URL to a dedicated loopback accounts_test_* database")
    with psycopg.connect(dsn) as conn:
        migrations = Path(__file__).resolve().parents[2] / "db/migrations/accounts"
        for migration in sorted(migrations.glob("*.sql")):
            conn.execute(migration.read_text(encoding="utf-8"))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='accounts_test_runtime'").fetchone():
            conn.execute("CREATE ROLE accounts_test_runtime LOGIN")
        grant_runtime(conn, "accounts_test_runtime")
    return Settings(
        database_url=make_conninfo(dsn, user="accounts_test_runtime"), secret="test-only-" + token(),
        public_origin="https://accounts.test", issuer="https://identity.test/pool",
        oidc_domain="https://login.test", client_id="client-test",
    )


@pytest.fixture
def store(settings):
    database = Store(settings)
    database.open()
    with psycopg.connect(os.environ["ACCOUNTS_TEST_DATABASE_URL"]) as conn:
        conn.execute("TRUNCATE app.users,app.access_requests,app.invitations,app.sessions,app.auth_flows,app.audit_events CASCADE")
    try:
        yield database
    finally:
        database.close()


class FakeIdentity:
    def __init__(self, settings):
        from services.accounts.identity import OIDC
        self.authorize_url = OIDC(settings, None).authorize_url

    async def exchange(self, code, verifier, nonce):
        return Identity("https://identity.test/pool", code, code + "@example.org", code)


class MailSink:
    def __init__(self):
        self.messages = []
        self.notifications = []
        self.fail = False

    def invite(self, email, raw):
        if self.fail:
            raise MailUnavailable("test_failure")
        self.messages.append((email, raw))

    def review(self, email, status, note):
        if self.fail:
            raise MailUnavailable("test_failure")
        self.notifications.append((email, status, note))


@pytest.fixture
def client(settings, store):
    mail = MailSink()
    app = create_app(settings, store=store, identity=FakeIdentity(settings), mailer=mail)
    with TestClient(app, base_url=settings.public_origin, client=("127.0.0.1", 42000)) as api:
        api.mail = mail
        yield api


@pytest.fixture
def account(settings, store):
    def make(subject="member", role="member", status="active"):
        raw = token()
        user = store.create_session(Identity(settings.issuer, subject, subject + "@example.org", subject), raw)
        with store.transaction() as conn:
            conn.execute("UPDATE app.users SET role=%s,status=%s WHERE id=%s", (role, status, user["id"]))
        return {
            "user": {**user, "role": role, "status": status}, "raw": raw,
            "headers": {"Cookie": "__Host-wildfire_session=" + raw, "Origin": settings.public_origin, "X-CSRF-Token": csrf(settings.secret, raw)},
        }
    return make
