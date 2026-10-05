"""Real PostgreSQL regressions for qualifications, concurrency and credentials."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import psycopg
import pytest

from services.accounts.identity import Identity
from services.accounts.security import FLOW_COOKIE, INVITE_COOKIE, SESSION_COOKIE, AccountError, digest, token


def test_pending_identity_cannot_get_admin_or_platform(client, account):
    applicant = account("pending", status="pending")
    assert client.get("/api/auth/me", headers=applicant["headers"]).json()["user"]["status"] == "pending"
    assert client.get("/api/admin/access-requests", headers=applicant["headers"]).status_code == 403
    headers = {**applicant["headers"], "X-Original-Method": "GET"}
    assert client.get("/internal/auth/active", headers=headers).status_code == 403


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/admin/users", "/api/admin/invitations", "/api/admin/audit-events"])
def test_missing_and_forged_cookie_rejected(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Cookie": SESSION_COOKIE + "=admin"}).status_code == 401


def test_member_cannot_admin_even_with_forged_role_header(client, account):
    member = account()
    headers = {**member["headers"], "X-Account-Role": "admin", "X-Account-User-Id": str(uuid4())}
    assert client.get("/api/admin/users", headers=headers).status_code == 403


@pytest.mark.parametrize("override", [{"X-CSRF-Token": ""}, {"X-CSRF-Token": "forged"}, {"Origin": "https://attacker.test"}, {"Origin": ""}])
def test_write_requires_csrf_and_origin(client, account, override):
    admin = account("admin", role="admin")
    response = client.post("/api/admin/invitations", headers={**admin["headers"], **override}, json={"email": "person@example.org"})
    assert response.status_code == 403
    assert not client.mail.messages


def test_non_ascii_csrf_is_rejected_without_server_error(client, account):
    admin = account("admin", role="admin")
    headers = {**admin["headers"], "X-CSRF-Token": b"\xff" * 64}
    response = client.post("/api/admin/invitations", headers=headers, json={"email": "person@example.org"})
    assert response.status_code == 403


def test_apply_approve_and_no_internal_note_leak(client, account, store):
    admin = account("admin", role="admin")
    applicant = account("applicant", status="pending")
    response = client.post("/api/access-requests", headers=applicant["headers"], json={"name": "Applicant", "organization": "Research", "purpose": "Compare historical data"})
    assert response.status_code == 201
    rid = response.json()["id"]
    duplicate = client.post("/api/access-requests", headers=applicant["headers"], json={"name": "Applicant", "organization": "Research", "purpose": "Again"})
    assert duplicate.status_code == 409
    decision = client.post(f"/api/admin/access-requests/{rid}/decision", headers=admin["headers"], json={"decision": "approve", "note": "INTERNAL", "public_note": "Welcome"})
    assert decision.status_code == 200
    assert client.mail.notifications == [("applicant@example.org", "approved", "Welcome")]
    own = client.get("/api/auth/me", headers=applicant["headers"])
    assert own.json()["user"]["status"] == "active"
    assert own.json()["application"]["public_note"] == "Welcome"
    assert "INTERNAL" not in own.text
    assert client.get("/internal/auth/active", headers={**applicant["headers"], "X-Original-Method": "GET"}).status_code == 204
    with store.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM app.audit_events WHERE action='access_approved'").fetchone()["n"] == 1


def test_rejection_can_be_reapplied_but_stays_pending(client, account):
    admin = account("admin", role="admin")
    applicant = account("applicant", status="pending")
    payload = {"name": "Person", "organization": "Research", "purpose": "Data"}
    rid = client.post("/api/access-requests", headers=applicant["headers"], json=payload).json()["id"]
    assert client.post(f"/api/admin/access-requests/{rid}/decision", headers=admin["headers"], json={"decision": "reject"}).status_code == 200
    assert client.get("/api/auth/me", headers=applicant["headers"]).json()["user"]["status"] == "pending"
    assert client.post("/api/access-requests", headers=applicant["headers"], json=payload).status_code == 201


def test_mail_failure_does_not_undo_approval_and_can_retry(client, account):
    admin = account("admin", role="admin")
    applicant = account("applicant", status="pending")
    rid = client.post("/api/access-requests", headers=applicant["headers"], json={"name":"Person","organization":"Research","purpose":"Data"}).json()["id"]
    client.mail.fail = True
    result = client.post(f"/api/admin/access-requests/{rid}/decision", headers=admin["headers"], json={"decision":"approve"})
    assert result.status_code == 200 and result.json()["notification_status"] == "failed"
    assert client.get("/api/auth/me", headers=applicant["headers"]).json()["user"]["status"] == "active"
    client.mail.fail = False
    assert client.post(f"/api/admin/access-requests/{rid}/notify", headers=admin["headers"]).json()["notification_status"] == "sent"


def test_application_is_bound_to_session_not_body(client, account):
    applicant = account("applicant", status="pending")
    response = client.post("/api/access-requests", headers=applicant["headers"], json={"name": "Person", "organization": "Research", "purpose": "Data", "email": "admin@example.org", "role": "admin"})
    assert response.status_code == 422
    assert client.get("/api/auth/me", headers=applicant["headers"]).json()["user"]["role"] == "member"


def test_person_cannot_read_other_person_request(client, account):
    a = account("a", status="pending")
    b = account("b", status="pending")
    client.post("/api/access-requests", headers=a["headers"], json={"name": "A", "organization": "PRIVATE-A", "purpose": "Data"})
    response = client.get("/api/access-requests/me", headers=b["headers"])
    assert response.json() == {"application": None}
    assert "PRIVATE-A" not in response.text


def test_concurrent_review_only_one_decision(store, account):
    admin = account("admin", role="admin")["user"]["id"]
    applicant = account("applicant", status="pending")["user"]["id"]
    request = store.apply(applicant, "Applicant", "Research", "Data")
    barrier = Barrier(2)
    def review():
        barrier.wait()
        try:
            store.decide(admin, request["id"], "approve", "", "")
            return 200
        except AccountError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: review(), range(2)))
    assert sorted(results) == [200, 409]


def test_concurrent_admin_demotion_preserves_one(store, account):
    admins = [account("admin1", role="admin")["user"]["id"], account("admin2", role="admin")["user"]["id"]]
    barrier = Barrier(2)
    def demote(user_id):
        barrier.wait()
        try:
            store.patch_user(user_id, user_id, "member", None)
            return 200
        except AccountError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(demote, admins)) == [200, 409]
    with store.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM app.users WHERE role='admin' AND status='active'").fetchone()["n"] == 1


def test_stale_admin_dependency_does_not_authorize_mutation(store, account):
    actor = account("former-admin", role="admin")["user"]["id"]
    second = account("admin2", role="admin")["user"]["id"]
    store.patch_user(second, actor, "member", None)
    with pytest.raises(AccountError) as result:
        store.create_invite(actor, "person@example.org", token())
    assert result.value.status == 403


def test_suspend_revokes_every_session(client, account, store, settings):
    admin = account("admin", role="admin")
    member = account("member")
    second_raw = token()
    store.create_session(Identity(settings.issuer, "member", "member@example.org", "Member"), second_raw)
    uid = member["user"]["id"]
    assert client.patch(f"/api/admin/users/{uid}", headers=admin["headers"], json={"status": "suspended"}).status_code == 200
    assert client.get("/api/auth/me", headers=member["headers"]).status_code == 401
    assert client.get("/api/auth/me", headers={"Cookie": SESSION_COOKIE + "=" + second_raw}).status_code == 401


def test_logout_revokes_and_can_repeat(client, account):
    member = account()
    response = client.post("/auth/logout", headers=member["headers"])
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/auth/me", headers=member["headers"]).status_code == 401
    client.cookies.clear()
    assert client.post("/auth/logout", headers={"Origin": "https://accounts.test"}).status_code == 204


@pytest.mark.parametrize("column,interval", [("last_seen_at", "31 minutes"), ("absolute_expires_at", "1 second")])
def test_idle_and_absolute_expiration(client, account, store, column, interval):
    member = account()
    with store.transaction() as conn:
        from psycopg import sql
        conn.execute(sql.SQL("UPDATE app.sessions SET {}=now()-%s::interval WHERE token_hash=%s").format(sql.Identifier(column)), (interval, digest(member["raw"])))
    assert client.get("/api/auth/me", headers=member["headers"]).status_code == 401


def test_invite_notification_failure_keeps_invitation(client, account):
    admin = account("admin", role="admin")
    client.mail.fail = True
    response = client.post("/api/admin/invitations", headers=admin["headers"], json={"email": "person@example.org"})
    assert response.status_code == 201
    assert response.json()["delivery_status"] == "failed"
    listing = client.get("/api/admin/invitations", headers=admin["headers"])
    assert len(listing.json()["items"]) == 1
    assert "token_hash" not in listing.text


def sign_in(client, subject):
    login = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    response = client.get("/auth/callback", params={"state": state, "code": subject}, follow_redirects=False)
    assert response.status_code == 302
    raw = client.cookies[SESSION_COOKIE]
    me = client.get("/api/auth/me").json()
    return {"Cookie": SESSION_COOKIE + "=" + raw, "Origin": "https://accounts.test", "X-CSRF-Token": me["csrf_token"]}


def test_cookie_flags_rotation_and_flow_replay(client):
    login = client.get("/auth/login", follow_redirects=False)
    assert "HttpOnly" in login.headers["set-cookie"] and "Secure" in login.headers["set-cookie"]
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    cookie = client.cookies[FLOW_COOKIE]
    response = client.get("/auth/callback", params={"state": state, "code": "new-user"}, follow_redirects=False)
    assert response.status_code == 302
    assert "session=" in response.headers["set-cookie"]
    assert client.get("/api/auth/me").json()["user"]["status"] == "pending"
    client.cookies.set(FLOW_COOKIE, cookie)
    assert client.get("/auth/callback", params={"state": state, "code": "new-user"}, follow_redirects=False).status_code == 401


def test_wrong_browser_cannot_use_stolen_login_state(client):
    login = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    client.cookies.clear()
    client.cookies.set(FLOW_COOKIE, "wrong")
    assert client.get("/auth/callback", params={"state": state, "code": "new-user"}, follow_redirects=False).status_code == 401


def test_stale_optional_invitation_cookie_does_not_block_normal_login(client):
    client.cookies.set(INVITE_COOKIE, "expired-claim", domain="accounts.test", path="/")
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert INVITE_COOKIE not in client.cookies


def test_sign_in_replaces_previous_browser_session(client, account):
    previous = account("previous")
    client.cookies.set(SESSION_COOKIE, previous["raw"], domain="accounts.test", path="/")
    sign_in(client, "new-user")
    assert client.get("/api/auth/me", headers=previous["headers"]).status_code == 401


def test_audit_failure_rolls_back_approval(store, account):
    from psycopg import sql
    import psycopg
    from services.accounts.config import Settings
    from services.accounts.store import Store
    admin = account("admin", role="admin")["user"]["id"]
    applicant = account("applicant", status="pending")["user"]["id"]
    request = store.apply(applicant, "Applicant", "Research", "Data")
    restricted_role = "accounts_test_restricted"
    with psycopg.connect(os.environ["ACCOUNTS_TEST_DATABASE_URL"], row_factory=psycopg.rows.dict_row) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (restricted_role,)).fetchone() is None:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(restricted_role)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA app TO {}").format(sql.Identifier(restricted_role)))
        conn.execute(sql.SQL("GRANT SELECT,UPDATE ON app.users,app.access_requests TO {}").format(sql.Identifier(restricted_role)))
        conn.execute(sql.SQL("GRANT SELECT ON app.sessions,app.auth_flows,app.invitations,app.audit_events TO {}").format(sql.Identifier(restricted_role)))
    from psycopg.conninfo import make_conninfo
    dsn = make_conninfo(store.settings.database_url, user=restricted_role)
    restricted = Store(Settings(
        database_url=dsn, secret=store.settings.secret, public_origin=store.settings.public_origin,
        issuer=store.settings.issuer, oidc_domain=store.settings.oidc_domain, client_id=store.settings.client_id,
    ))
    restricted.open()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            restricted.decide(admin, request["id"], "approve", "", "")
    finally:
        restricted.close()
    with store.transaction() as conn:
        assert conn.execute("SELECT status FROM app.users WHERE id=%s", (applicant,)).fetchone()["status"] == "pending"
        assert conn.execute("SELECT status FROM app.access_requests WHERE id=%s", (request["id"],)).fetchone()["status"] == "pending"


def test_runtime_role_cannot_change_schema_or_audit_history(store):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store.transaction() as conn:
            conn.execute("CREATE TABLE app.must_not_create(id int)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store.transaction() as conn:
            conn.execute("DELETE FROM app.audit_events")


def test_privileged_connection_cannot_start_service(settings):
    from dataclasses import replace
    from services.accounts.store import Store
    privileged = Store(replace(settings, database_url=os.environ["ACCOUNTS_TEST_DATABASE_URL"]))
    try:
        with pytest.raises(RuntimeError, match="restricted accounts runtime role"):
            privileged.open()
    finally:
        privileged.close()


@pytest.mark.parametrize("destination", ["//attacker.test", "https://attacker.test", "http://[", "/\\attacker.test", "/admin?token=secret", "/%2f%2fattacker.test"])
def test_no_open_redirect(client, destination):
    assert client.get("/auth/login", params={"return_to": destination}, follow_redirects=False).status_code == 422


def test_invite_claim_does_not_consume_and_accept_matches_email(client, account, store):
    admin = account("admin", role="admin")
    response = client.post("/api/admin/invitations", headers=admin["headers"], json={"email": "invited@example.org"})
    raw = client.mail.messages[-1][1]
    assert client.post("/api/invitations/claim", headers={"Origin": "https://accounts.test"}, json={"token": raw}).status_code == 200
    with store.transaction() as conn:
        assert conn.execute("SELECT accepted_at FROM app.invitations WHERE id=%s", (response.json()["id"],)).fetchone()["accepted_at"] is None
    headers = sign_in(client, "wrong-person")
    assert client.post("/api/invitations/accept", headers=headers).status_code == 403
    client.cookies.clear()
    client.post("/api/invitations/claim", headers={"Origin": "https://accounts.test"}, json={"token": raw})
    headers = sign_in(client, "invited")
    assert client.post("/api/invitations/accept", headers=headers).json()["status"] == "active"
    assert client.post("/api/invitations/accept", headers=headers).status_code == 410


def test_resend_invalidates_token_and_previously_claimed_session(client, account):
    admin = account("admin", role="admin")
    invitation = client.post("/api/admin/invitations", headers=admin["headers"], json={"email": "invited@example.org"}).json()
    old_raw = client.mail.messages[-1][1]
    client.post("/api/invitations/claim", headers={"Origin": "https://accounts.test"}, json={"token": old_raw})
    headers = sign_in(client, "invited")
    assert client.post(f"/api/admin/invitations/{invitation['id']}/resend", headers=admin["headers"]).status_code == 200
    assert client.post("/api/invitations/accept", headers=headers).status_code == 410
    assert client.post("/api/invitations/claim", headers={"Origin": "https://accounts.test"}, json={"token": old_raw}).status_code == 410


def test_invite_does_not_restore_suspended_account(client, account):
    admin = account("admin", role="admin")
    account("invited", status="suspended")
    client.post("/api/admin/invitations", headers=admin["headers"], json={"email": "invited@example.org"})
    raw = client.mail.messages[-1][1]
    client.post("/api/invitations/claim", headers={"Origin": "https://accounts.test"}, json={"token": raw})
    headers = sign_in(client, "invited")
    assert client.post("/api/invitations/accept", headers=headers).status_code == 403


def test_internal_gateway_requires_original_method_and_csrf(client, account):
    member = account()
    assert client.get("/internal/auth/active", headers=member["headers"]).status_code == 403
    assert client.get("/internal/auth/active", headers={**member["headers"], "X-Original-Method": "POST"}).status_code == 204
    bad = {**member["headers"], "X-Original-Method": "POST", "X-CSRF-Token": ""}
    assert client.get("/internal/auth/active", headers=bad).status_code == 403


def test_error_does_not_echo_credentials(client):
    submitted = "DO-NOT-ECHO-AUTH-TOKEN"
    response = client.post("/api/invitations/claim", headers={"Origin": "https://accounts.test"}, json={"token": submitted})
    assert response.status_code == 422 and submitted not in response.text


def test_database_fault_fails_closed_without_secret(client, account, monkeypatch):
    member = account()
    def fail(_):
        raise psycopg.OperationalError("secret database password")
    monkeypatch.setattr(client.app.state.store, "authenticate", fail)
    response = client.get("/internal/auth/active", headers={**member["headers"], "X-Original-Method": "GET"})
    assert response.status_code == 503
    assert "secret database password" not in response.text


def test_pagination_is_bounded_and_stable(client, account):
    admin = account("admin", role="admin")
    for index in range(3):
        client.post("/api/admin/invitations", headers=admin["headers"], json={"email": f"person{index}@example.org"})
    page = client.get("/api/admin/invitations", params={"limit": 2}, headers=admin["headers"]).json()
    next_page = client.get("/api/admin/invitations", params={"limit": 2, "cursor": page["next_cursor"]}, headers=admin["headers"]).json()
    assert len(page["items"]) == 2 and len(next_page["items"]) == 1
    assert not ({r["id"] for r in page["items"]} & {r["id"] for r in next_page["items"]})
    assert client.get("/api/admin/users", params={"limit": 101}, headers=admin["headers"]).status_code == 422


def test_session_and_login_secrets_not_stored_plaintext(client, account, store):
    member = account()
    login = client.get("/auth/login", follow_redirects=False)
    raw = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    with store.transaction() as conn:
        session = conn.execute("SELECT token_hash FROM app.sessions WHERE user_id=%s", (member["user"]["id"],)).fetchone()
        flow = conn.execute("SELECT token_hash,sealed_payload FROM app.auth_flows").fetchone()
    assert session["token_hash"] == digest(member["raw"]) and session["token_hash"] != member["raw"]
    assert flow["token_hash"] == digest(raw) and flow["token_hash"] != raw
    assert not flow["sealed_payload"].startswith("{")
