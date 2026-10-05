"""PostgreSQL account transactions, independent of warehouse loaders."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from psycopg import sql
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from services.accounts.config import Settings
from services.accounts.identity import Identity
from services.accounts.security import AccountError, digest

AUTHORIZATION_LOCK = 174832092

# Each administrator list reads one table under an alias, with the people its ids
# point to beside them. list_rows drops token_hash before anything leaves.
ADMIN_LISTS = {
    "access_requests": (
        "SELECT r.*, u.name AS applicant_name, u.email AS applicant_email, u.status AS applicant_status, "
        "v.email AS reviewer_email FROM app.access_requests r JOIN app.users u ON u.id=r.applicant_id "
        "LEFT JOIN app.users v ON v.id=r.reviewer_id", "r"),
    "invitations": (
        "SELECT i.*, u.email AS invited_by_email, a.email AS accepted_by_email FROM app.invitations i "
        "LEFT JOIN app.users u ON u.id=i.invited_by LEFT JOIN app.users a ON a.id=i.accepted_by", "i"),
    "users": ("SELECT u.* FROM app.users u", "u"),
    "audit_events": (
        "SELECT e.*, actor.email AS actor_email, COALESCE(target_user.email, applicant.email, invitation.email) AS target_email "
        "FROM app.audit_events e LEFT JOIN app.users actor ON actor.id=e.actor_id "
        "LEFT JOIN app.users target_user ON e.target_type='user' AND target_user.id=e.target_id "
        "LEFT JOIN app.access_requests request ON e.target_type='access_request' AND request.id=e.target_id "
        "LEFT JOIN app.users applicant ON applicant.id=request.applicant_id "
        "LEFT JOIN app.invitations invitation ON e.target_type='invitation' AND invitation.id=e.target_id", "e"),
}


def column(alias: str, name: str) -> sql.Composable:
    return sql.SQL("{}.{}").format(sql.Identifier(alias), sql.Identifier(name))


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool = ConnectionPool(
            settings.database_url, min_size=1, max_size=5, open=False,
            timeout=10, name="accounts", kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
        )

    def open(self) -> None:
        self.pool.open(wait=True, timeout=10)
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT to_regclass('app.' || name) AS name FROM "
                "unnest(ARRAY['users','sessions','auth_flows','access_requests','invitations','audit_events']) AS name"
            ).fetchall()
            if any(row["name"] is None for row in rows):
                raise RuntimeError("Apply the accounts migration before starting the service")
            privileges = conn.execute(
                "SELECT rolsuper,rolcreaterole,has_schema_privilege(current_user,'app','CREATE') AS schema_create,"
                "has_database_privilege(current_user,current_database(),'CREATE') AS database_create "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if any(privileges.values()):
                raise RuntimeError("Use a restricted accounts runtime role, not the migration or database-owner role")

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def transaction(self):
        with self.pool.connection() as conn, conn.transaction():
            yield conn

    def health(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("SELECT 1").fetchone()

    @staticmethod
    def audit(conn, actor: UUID | None, action: str, kind: str, target: UUID, metadata: dict | None = None):
        conn.execute(
            "INSERT INTO app.audit_events(actor_id, action, target_type, target_id, metadata) VALUES (%s,%s,%s,%s,%s)",
            (actor, action, kind, target, Jsonb(metadata or {})),
        )

    @staticmethod
    def admin(conn, actor: UUID) -> None:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (AUTHORIZATION_LOCK,))
        user = conn.execute("SELECT role,status FROM app.users WHERE id=%s FOR UPDATE", (actor,)).fetchone()
        if not user or user["role"] != "admin" or user["status"] != "active":
            raise AccountError(403, "admin_required", "Administrator access is required.")

    def save_flow(self, kind: str, raw: str, browser: str, sealed: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM app.auth_flows WHERE expires_at < now()")
            conn.execute(
                "INSERT INTO app.auth_flows(kind,token_hash,browser_hash,sealed_payload,expires_at) "
                "VALUES (%s,%s,%s,%s,now()+make_interval(secs=>%s))",
                (kind, digest(raw), digest(browser), sealed, self.settings.flow_seconds),
            )

    def take_flow(self, kind: str, raw: str, browser: str) -> str:
        with self.transaction() as conn:
            row = conn.execute(
                "UPDATE app.auth_flows SET consumed_at=now() WHERE kind=%s AND token_hash=%s "
                "AND browser_hash=%s AND consumed_at IS NULL AND expires_at>now() RETURNING sealed_payload",
                (kind, digest(raw), digest(browser)),
            ).fetchone()
            if row is None:
                raise AccountError(401, "invalid_login_flow", "Please start signing in again.")
            return row["sealed_payload"]

    def create_session(self, identity: Identity, raw: str, invitation_id: UUID | None = None, invitation_hash: str | None = None, previous_token: str | None = None) -> dict:
        with self.transaction() as conn:
            user = conn.execute(
                "INSERT INTO app.users(issuer,subject,email,name) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (issuer,subject) DO UPDATE SET email=EXCLUDED.email,"
                "name=CASE WHEN EXCLUDED.name='' THEN app.users.name ELSE EXCLUDED.name END,updated_at=now() RETURNING *",
                (identity.issuer, identity.subject, identity.email, identity.name),
            ).fetchone()
            conn.execute(
                "INSERT INTO app.sessions(token_hash,user_id,invitation_id,invitation_hash,absolute_expires_at) "
                "VALUES (%s,%s,%s,%s,now()+make_interval(secs=>%s))",
                (digest(raw), user["id"], invitation_id, invitation_hash, self.settings.session_seconds),
            )
            if previous_token:
                conn.execute("UPDATE app.sessions SET revoked_at=now() WHERE token_hash=%s", (digest(previous_token),))
            return user

    def authenticate(self, raw: str) -> dict:
        with self.transaction() as conn:
            row = conn.execute(
                "UPDATE app.sessions s SET last_seen_at=now() FROM app.users u "
                "WHERE s.user_id=u.id AND s.token_hash=%s AND s.revoked_at IS NULL "
                "AND s.absolute_expires_at>now() AND s.last_seen_at>now()-make_interval(secs=>%s) "
                "RETURNING u.*,s.id AS session_id,s.invitation_id",
                (digest(raw), self.settings.idle_seconds),
            ).fetchone()
            if row is None:
                raise AccountError(401, "session_expired", "Please sign in again.")
            return row

    def logout(self, raw: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE app.sessions SET revoked_at=now() WHERE token_hash=%s", (digest(raw),))

    def application(self, user_id: UUID) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT id,organization,purpose,status,public_note,created_at,reviewed_at "
                "FROM app.access_requests WHERE applicant_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                (user_id,),
            ).fetchone()

    def apply(self, user_id: UUID, name: str, organization: str, purpose: str) -> dict:
        try:
            with self.transaction() as conn:
                user = conn.execute("SELECT status FROM app.users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()
                if not user or user["status"] != "pending":
                    raise AccountError(403, "application_unavailable", "This account cannot submit an access request.")
                conn.execute("UPDATE app.users SET name=%s,updated_at=now() WHERE id=%s", (name, user_id))
                row = conn.execute(
                    "INSERT INTO app.access_requests(applicant_id,organization,purpose) VALUES (%s,%s,%s) RETURNING id,status,created_at",
                    (user_id, organization, purpose),
                ).fetchone()
                self.audit(conn, user_id, "access_requested", "access_request", row["id"])
                return row
        except UniqueViolation as exc:
            raise AccountError(409, "request_pending", "An access request is already awaiting review.") from exc

    def active_admin_emails(self) -> list[str]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT email FROM app.users WHERE role='admin' AND status='active' ORDER BY email").fetchall()
            return [row["email"] for row in rows]

    def list_rows(self, actor: UUID, table: str, cursor: UUID | None, limit: int, status: str | None = None, search: str | None = None) -> dict:
        if table not in ADMIN_LISTS:
            raise ValueError("Unsupported accounts list")
        select, alias = ADMIN_LISTS[table]
        with self.pool.connection() as conn:
            user = conn.execute("SELECT role,status FROM app.users WHERE id=%s", (actor,)).fetchone()
            if not user or user["role"] != "admin" or user["status"] != "active":
                raise AccountError(403, "admin_required", "Administrator access is required.")
            before = None
            if cursor is not None:
                before = conn.execute(sql.SQL("SELECT created_at,id FROM app.{} WHERE id=%s").format(sql.Identifier(table)), (cursor,)).fetchone()
                if before is None:
                    raise AccountError(422, "invalid_cursor", "The page cursor is not valid.")
            clauses, params = [], []
            if before:
                clauses.append(sql.SQL("({},{})<(%s,%s)").format(column(alias, "created_at"), column(alias, "id")))
                params.extend((before["created_at"], before["id"]))
            if status is not None:
                clauses.append(sql.SQL("{}=%s").format(column(alias, "status")))
                params.append(status)
            if search:
                # Literal text: % and _ in the search do not act as wildcards.
                pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                clauses.append(sql.SQL("({} ILIKE %s OR {} ILIKE %s)").format(column(alias, "email"), column(alias, "name")))
                params.extend((pattern, pattern))
            where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")
            order = sql.SQL(" ORDER BY {} DESC,{} DESC LIMIT %s").format(column(alias, "created_at"), column(alias, "id"))
            rows = conn.execute(sql.SQL(select) + where + order, (*params, limit + 1)).fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            for row in rows:
                row.pop("token_hash", None)
            return {"items": rows, "next_cursor": str(rows[-1]["id"]) if more else None}

    def decide(self, actor: UUID, request_id: UUID, decision: str, note: str, public_note: str) -> dict:
        with self.transaction() as conn:
            self.admin(conn, actor)
            request = conn.execute("SELECT * FROM app.access_requests WHERE id=%s FOR UPDATE", (request_id,)).fetchone()
            if not request:
                raise AccountError(404, "request_missing", "The access request was not found.")
            if request["status"] != "pending":
                raise AccountError(409, "request_decided", "This request has already been reviewed.")
            applicant = conn.execute("SELECT status,email FROM app.users WHERE id=%s FOR UPDATE", (request["applicant_id"],)).fetchone()
            if applicant["status"] != "pending":
                raise AccountError(409, "account_changed", "The account status has changed. Please refresh.")
            status = "approved" if decision == "approve" else "rejected"
            result = conn.execute(
                "UPDATE app.access_requests SET status=%s,reviewer_id=%s,review_note=%s,public_note=%s,reviewed_at=now() "
                "WHERE id=%s RETURNING id,status,reviewed_at",
                (status, actor, note, public_note, request_id),
            ).fetchone()
            if decision == "approve":
                conn.execute("UPDATE app.users SET status='active',updated_at=now() WHERE id=%s", (request["applicant_id"],))
            self.audit(conn, actor, "access_" + status, "access_request", request_id)
            result["_recipient"] = applicant["email"]
            return result

    def review_delivery(self, request_id: UUID, status: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE app.access_requests SET notification_status=%s WHERE id=%s", (status, request_id))

    def review_notification(self, actor: UUID, request_id: UUID) -> dict:
        with self.transaction() as conn:
            self.admin(conn, actor)
            row = conn.execute(
                "SELECT r.id,r.status,r.public_note,u.email FROM app.access_requests r "
                "JOIN app.users u ON u.id=r.applicant_id WHERE r.id=%s",
                (request_id,),
            ).fetchone()
            if not row:
                raise AccountError(404, "request_missing", "The access request was not found.")
            if row["status"] == "pending":
                raise AccountError(409, "request_pending", "Review the request before sending a result.")
            self.audit(conn, actor, "review_notification_requested", "access_request", request_id)
            return row

    def create_invite(self, actor: UUID, email: str, raw: str) -> dict:
        with self.transaction() as conn:
            self.admin(conn, actor)
            row = conn.execute(
                "INSERT INTO app.invitations(email,invited_by,token_hash,expires_at) "
                "VALUES (%s,%s,%s,now()+make_interval(secs=>%s)) RETURNING id,email,expires_at,delivery_status",
                (email, actor, digest(raw), self.settings.invitation_seconds),
            ).fetchone()
            self.audit(conn, actor, "invitation_created", "invitation", row["id"])
            return row

    def update_invite(self, actor: UUID, invitation_id: UUID, raw: str | None) -> dict:
        with self.transaction() as conn:
            self.admin(conn, actor)
            row = conn.execute("SELECT * FROM app.invitations WHERE id=%s FOR UPDATE", (invitation_id,)).fetchone()
            if not row:
                raise AccountError(404, "invitation_missing", "The invitation was not found.")
            if row["accepted_at"] or row["revoked_at"]:
                raise AccountError(409, "invitation_closed", "This invitation is no longer available.")
            if raw is None:
                conn.execute("UPDATE app.invitations SET revoked_at=now() WHERE id=%s", (invitation_id,))
                action = "invitation_revoked"
            else:
                conn.execute(
                    "UPDATE app.invitations SET token_hash=%s,expires_at=now()+make_interval(secs=>%s),delivery_status='pending' WHERE id=%s",
                    (digest(raw), self.settings.invitation_seconds, invitation_id),
                )
                action = "invitation_resent"
            self.audit(conn, actor, action, "invitation", invitation_id)
            return {"id": row["id"], "email": row["email"]}

    def delivery(self, invitation_id: UUID, raw: str, status: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE app.invitations SET delivery_status=%s WHERE id=%s AND token_hash=%s",
                (status, invitation_id, digest(raw)),
            )

    def claim_invite(self, raw: str) -> dict:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT id,token_hash FROM app.invitations WHERE token_hash=%s AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>now()",
                (digest(raw),),
            ).fetchone()
            if row is None:
                raise AccountError(410, "invitation_unavailable", "This invitation is no longer available.")
            return row

    def accept_invite(self, user_id: UUID, session_id: UUID) -> dict:
        with self.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (AUTHORIZATION_LOCK,))
            user = conn.execute("SELECT * FROM app.users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()
            session = conn.execute("SELECT invitation_id,invitation_hash,revoked_at,absolute_expires_at>now() AS available FROM app.sessions WHERE id=%s AND user_id=%s FOR UPDATE", (session_id, user_id)).fetchone()
            if not session or session["revoked_at"] or not session["available"] or not session["invitation_id"]:
                raise AccountError(410, "invitation_unavailable", "Please open the invitation and sign in again.")
            invite = conn.execute("SELECT *,expires_at>now() AS available FROM app.invitations WHERE id=%s FOR UPDATE", (session["invitation_id"],)).fetchone()
            if not invite or invite["accepted_at"] or invite["revoked_at"] or not invite["available"] or invite["token_hash"] != session["invitation_hash"]:
                raise AccountError(410, "invitation_unavailable", "This invitation is no longer available.")
            if user["email"] != invite["email"] or user["status"] == "suspended":
                raise AccountError(403, "invitation_identity_mismatch", "This account cannot accept this invitation.")
            conn.execute("UPDATE app.invitations SET accepted_by=%s,accepted_at=now() WHERE id=%s", (user_id, invite["id"]))
            conn.execute("UPDATE app.users SET status='active',updated_at=now() WHERE id=%s", (user_id,))
            pending = conn.execute(
                "UPDATE app.access_requests SET status='approved',reviewer_id=%s,reviewed_at=now() "
                "WHERE applicant_id=%s AND status='pending' RETURNING id",
                (invite["invited_by"], user_id),
            ).fetchall()
            for request in pending:
                self.audit(conn, user_id, "access_granted_by_invitation", "access_request", request["id"], {"invitation_id": str(invite["id"])})
            conn.execute("UPDATE app.sessions SET invitation_id=NULL,invitation_hash=NULL WHERE id=%s", (session_id,))
            self.audit(conn, user_id, "invitation_accepted", "invitation", invite["id"])
            return {"status": "active"}

    def patch_user(self, actor: UUID, user_id: UUID, role: str | None, status: str | None) -> dict:
        with self.transaction() as conn:
            self.admin(conn, actor)
            user = conn.execute("SELECT * FROM app.users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()
            if not user:
                raise AccountError(404, "user_missing", "The account was not found.")
            if user["status"] == "pending":
                raise AccountError(409, "approval_required", "Review the access request or send an invitation first.")
            new_role, new_status = role or user["role"], status or user["status"]
            if user["role"] == "admin" and user["status"] == "active" and (new_role != "admin" or new_status != "active"):
                count = conn.execute("SELECT count(*) AS n FROM app.users WHERE role='admin' AND status='active'").fetchone()["n"]
                if count == 1:
                    raise AccountError(409, "last_admin", "At least one active administrator must remain.")
            row = conn.execute(
                "UPDATE app.users SET role=%s,status=%s,updated_at=now() WHERE id=%s RETURNING id,email,name,role,status",
                (new_role, new_status, user_id),
            ).fetchone()
            if new_status == "suspended":
                conn.execute("UPDATE app.sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
            self.audit(conn, actor, "account_updated", "user", user_id, {"role": new_role, "status": new_status})
            return row

    def bootstrap_admin(self, user_id: UUID) -> None:
        with self.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (AUTHORIZATION_LOCK,))
            row = conn.execute("UPDATE app.users SET role='admin',status='active',updated_at=now() WHERE id=%s RETURNING id", (user_id,)).fetchone()
            if row is None:
                raise AccountError(404, "user_missing", "Sign in with the verified identity before bootstrapping.")
            self.audit(conn, None, "admin_bootstrapped", "user", user_id)
