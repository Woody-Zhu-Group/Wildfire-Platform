"""Explicit migration, restricted runtime grants and trusted admin bootstrap."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql

from services.accounts.config import Settings
from services.accounts.store import Store

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations" / "accounts"


def grant_runtime(conn, role_name: str) -> None:
    role = sql.Identifier(role_name)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA app TO {}").format(role))
    for table in ("users", "access_requests", "invitations"):
        conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON app.{} TO {}").format(sql.Identifier(table), role))
    for table in ("sessions", "auth_flows"):
        conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON app.{} TO {}").format(sql.Identifier(table), role))
    conn.execute(sql.SQL("GRANT SELECT,INSERT ON app.audit_events TO {}").format(role))


def main() -> None:
    from shared.db import load_env

    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    grants = commands.add_parser("grant-runtime")
    grants.add_argument("--role", required=True)
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--user-id", type=UUID, required=True)
    commands.add_parser("prune")
    args = parser.parse_args()
    if args.command in {"migrate", "grant-runtime"}:
        dsn = os.environ.get("ACCOUNTS_MIGRATION_DATABASE_URL")
        if not dsn:
            parser.error("Set ACCOUNTS_MIGRATION_DATABASE_URL; the warehouse connection is never used implicitly")
        with psycopg.connect(dsn) as conn:
            if args.command == "migrate":
                for migration in sorted(MIGRATIONS.glob("*.sql")):
                    conn.execute(migration.read_text(encoding="utf-8"))
                print("Accounts migration applied.")
            else:
                grant_runtime(conn, args.role)
                print("Restricted accounts runtime grants applied to the existing role.")
        return
    settings = Settings.from_env()
    store = Store(settings)
    try:
        store.open()
        if args.command == "bootstrap-admin":
            store.bootstrap_admin(args.user_id)
            print("Verified account promoted to active administrator; audit event recorded.")
        else:
            with store.transaction() as conn:
                conn.execute("DELETE FROM app.auth_flows WHERE expires_at<now()")
                conn.execute(
                    "DELETE FROM app.sessions WHERE absolute_expires_at<now()-interval '7 days' "
                    "OR revoked_at<now()-interval '7 days'"
                )
            print("Expired login flows and old invalid sessions removed; account and audit history retained.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
