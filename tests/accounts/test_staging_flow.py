"""Browser walk through the signed-in website on the local staging harness, never production.

Start tests/accounts/staging_harness.py first, then:

    ACCOUNTS_STAGING_URL=https://localhost:8443 pytest tests/accounts/test_staging_flow.py

Needs Python Playwright with Chromium. The harness's development sign-in page
stands in for Cognito; no model, mail service or production data is touched.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
POLICY = json.loads((Path(__file__).resolve().parents[2] / "deploy/cloudfront/security-headers.json").read_text(encoding="utf-8"))
CSP = POLICY["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]
CSP_VIOLATIONS: list[str] = []


@pytest.fixture(autouse=True)
def no_csp_violations():
    """The harness sends the production CSP; nothing the site does may break it."""
    CSP_VIOLATIONS.clear()
    yield
    assert not CSP_VIOLATIONS, CSP_VIOLATIONS


@pytest.fixture(scope="module")
def base():
    url = os.environ.get("ACCOUNTS_STAGING_URL", "")
    if not url:
        pytest.skip("Run the documented local staging harness for the browser flow")
    if urlsplit(url).hostname not in {"localhost", "127.0.0.1"}:
        pytest.fail("The staging flow only runs against the local harness")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def person(browser, base):
    context = browser.new_context(ignore_https_errors=True, base_url=base, viewport={"width": 1280, "height": 860})
    page = context.new_page()
    page.set_default_timeout(20_000)
    page.on("console", lambda message: CSP_VIOLATIONS.append(message.text) if "Content Security Policy" in message.text else None)
    return context, page


def identity_page(page, email, name=""):
    expect(page.get_by_role("heading", name="Development sign-in")).to_be_visible()
    page.fill("input[name=email]", email)
    page.fill("input[name=name]", name)
    page.get_by_role("button", name="Sign in").click()


def sign_in(page, email):
    page.goto("/")
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("link", name="Continue to sign in").click()
    identity_page(page, email)


def account(page):
    return page.request.get("/api/auth/me").json()


def write(page, base, method, path, body=None):
    csrf = account(page)["csrf_token"]
    response = page.request.fetch(path, method=method, data=json.dumps(body) if body is not None else None,
                                  headers={"Origin": base, "X-CSRF-Token": csrf, "Content-Type": "application/json"})
    assert response.ok, f"{method} {path}: {response.status} {response.text()}"
    return response.json() if response.status != 204 else None


def mail_to(page, email):
    return [message for message in page.request.get("/dev-idp/mail").json() if message["to"] == email]


def test_signed_out_visitor_sees_landing_and_no_data(browser, base):
    context, page = person(browser, base)
    response = page.goto("/workspace")
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-frame-options"] == "DENY"
    expect(page.get_by_role("heading", name="California wildfire and utility data, in one workspace.")).to_be_visible()
    assert page.request.get("/api/auth/me").status == 401
    assert page.request.get("/api/data-query/summary?dataset=cpuc_ignitions&start_date=2024-01-01&end_date=2024-12-31").status == 401
    assert page.request.post("/api/agent/ask/stream", data='{"question":"x"}', headers={"Content-Type": "application/json"}).status == 401
    context.close()


def test_request_review_invitation_and_session_end(browser, base):
    admin_context, admin = person(browser, base)
    sign_in(admin, "admin@example.org")
    expect(admin).to_have_url(f"{base}/access-status")
    assert admin.request.post("/dev-idp/bootstrap-admin", form={"email": "admin@example.org"}).ok
    admin.reload()
    expect(admin).to_have_url(f"{base}/workspace")
    expect(admin.get_by_text("admin@example.org")).to_be_visible()

    # A new person creates an account, applies, and cannot read data while pending.
    alice_context, alice = person(browser, base)
    alice.goto("/")
    alice.get_by_role("button", name="Request access").first.click()
    alice.get_by_role("link", name="Create an account").click()
    identity_page(alice, "alice@example.org")
    expect(alice).to_have_url(f"{base}/access-status")
    alice.get_by_label("Full name").fill("Alice Example")
    alice.get_by_label("Organization").fill("Example Policy Lab")
    alice.get_by_label("How you plan to use the platform").fill("Reviewing ignition trends.")
    alice.get_by_role("button", name="Submit request").click()
    expect(alice.get_by_role("heading", name="Your request is under review")).to_be_visible()
    assert alice.request.get("/api/data-query/summary?dataset=cpuc_ignitions&start_date=2024-01-01&end_date=2024-12-31").status == 403
    alice_id = account(alice)["user"]["id"]

    # The administrator approves; the decision is mailed; the workspace opens with live data.
    pending = admin.request.get("/api/admin/access-requests?status=pending").json()["items"]
    request_id = next(row["id"] for row in pending if row["applicant_id"] == alice_id)
    write(admin, base, "POST", f"/api/admin/access-requests/{request_id}/decision", {"decision": "approve", "public_note": "Welcome."})
    assert [m["status"] for m in mail_to(admin, "alice@example.org")] == ["approved"]
    with alice.expect_response(lambda r: "/api/visualization/" in r.url and r.status == 200, timeout=60_000):
        alice.reload()
    expect(alice).to_have_url(f"{base}/workspace")
    seen = alice.request.get("/dev-idp/seen").json()
    assert seen["visualization"] == alice_id, "the gateway passes the verified user, not the caller's header"

    # Ask is a POST: it passes the gateway only with the session's CSRF token.
    alice.get_by_label("Ask a question").fill("How many CPUC ignitions in 2024?")
    alice.get_by_label("Ask a question").press("Enter")
    expect(alice.get_by_text("Staging agent: no model was called.", exact=False)).to_be_visible()
    forged = alice.request.post("/api/agent/ask/stream", data='{"question":"x"}', headers={"Content-Type": "application/json", "Origin": base})
    assert forged.status == 403

    # An invitation opens the workspace for the invited address only.
    write(admin, base, "POST", "/api/admin/invitations", {"email": "bob@example.org"})
    write(admin, base, "POST", "/api/admin/invitations", {"email": "carol@example.org"})
    bob_context, bob = person(browser, base)
    bob.goto(mail_to(admin, "bob@example.org")[-1]["link"])
    identity_page(bob, "bob@example.org")
    expect(bob).to_have_url(f"{base}/workspace", timeout=30_000)
    assert "token" not in bob.evaluate("JSON.stringify(history.state) + location.href")
    dave_context, dave = person(browser, base)
    dave.goto(mail_to(admin, "carol@example.org")[-1]["link"])
    identity_page(dave, "dave@example.org")
    expect(dave.get_by_role("heading", name="This invitation is for a different email")).to_be_visible()

    # Suspension ends the session: the next data request returns the visitor to the landing page.
    # Let the panels finish loading first, or one of their requests is that next request.
    alice.wait_for_load_state("networkidle")
    write(admin, base, "PATCH", f"/api/admin/users/{alice_id}", {"status": "suspended"})
    alice.get_by_label("Workspace year").select_option("2023")
    expect(alice.get_by_text("Your session ended. Sign in again to continue.")).to_be_visible()
    # The address keeps /workspace, so signing in again returns there.
    expect(alice).to_have_url(f"{base}/workspace")
    expect(alice.get_by_role("heading", name="California wildfire and utility data, in one workspace.")).to_be_visible()
    sign_in(alice, "alice@example.org")
    expect(alice.get_by_role("heading", name="This account is suspended")).to_be_visible()

    # Sign-out revokes the session and leaves no workspace behind the back button.
    bob.get_by_role("button", name="Sign out").click()
    expect(bob).to_have_url(f"{base}/")
    assert bob.request.get("/api/auth/me").status == 401
    bob.go_back()
    expect(bob.get_by_role("heading", name="California wildfire and utility data, in one workspace.")).to_be_visible()

    for context in (admin_context, alice_context, bob_context, dave_context):
        context.close()


def test_console_reviews_invites_and_manages_members(browser, base):
    lead_context, lead = person(browser, base)
    sign_in(lead, "lead@example.org")
    assert lead.request.post("/dev-idp/bootstrap-admin", form={"email": "lead@example.org"}).ok
    lead.reload()
    lead.get_by_role("link", name="Console").click()
    expect(lead).to_have_url(f"{base}/admin")
    expect(lead.get_by_text("No requests are waiting for review.")).to_be_visible()

    erin_context, erin = person(browser, base)
    erin.goto("/")
    erin.get_by_role("button", name="Request access").first.click()
    erin.get_by_role("link", name="Create an account").click()
    identity_page(erin, "erin@example.org")
    erin.get_by_label("Full name").fill("Erin Example")
    erin.get_by_label("Organization").fill("Example Utility Lab")
    erin.get_by_label("How you plan to use the platform").fill("Comparing EPSS outages by cause.")
    erin.get_by_role("button", name="Submit request").click()
    expect(erin.get_by_role("heading", name="Your request is under review")).to_be_visible()
    alerts = [m for m in mail_to(lead, "lead@example.org") if m["kind"] == "access_requested"]
    assert [m["applicant"] for m in alerts] == ["erin@example.org"], "every active administrator hears of a new request"

    # Requests: the reviewer sees who applied and why, and the decision is mailed with the message.
    lead.reload()
    request = lead.get_by_role("listitem").filter(has_text="erin@example.org")
    expect(request).to_contain_text("Erin Example")
    expect(request).to_contain_text("Comparing EPSS outages by cause.")
    request.get_by_role("button", name="Approve").click()
    dialog = lead.get_by_role("dialog")
    dialog.get_by_label("Message to the applicant").fill("Welcome aboard.")
    dialog.get_by_role("button", name="Approve").click()
    expect(lead.get_by_role("status")).to_contain_text("Approved erin@example.org. The applicant was emailed.")
    assert [(m["status"], m["note"]) for m in mail_to(lead, "erin@example.org")] == [("approved", "Welcome aboard.")]
    lead.get_by_role("group", name="Request status").get_by_role("button", name="Approved").click()
    expect(lead.get_by_role("listitem").filter(has_text="erin@example.org")).to_contain_text("lead@example.org")

    # Invitations: send, see the state, revoke.
    lead.get_by_role("tab", name="Invitations").click()
    for address in ("frank@example.org", "gina@example.org"):
        lead.get_by_label("Email address to invite").fill(address)
        lead.get_by_role("button", name="Send invitation").click()
        expect(lead.get_by_role("status")).to_contain_text(f"Invitation emailed to {address}.")
    gina = lead.get_by_role("listitem").filter(has_text="gina@example.org")
    expect(gina).to_contain_text("Waiting")
    gina.get_by_role("button", name="Revoke").click()
    lead.get_by_role("dialog").get_by_role("button", name="Revoke").click()
    expect(lead.get_by_role("listitem").filter(has_text="gina@example.org")).to_contain_text("Revoked")

    # Members: search, promote and demote, suspend and restore; the administrator's own row has no switches.
    lead.get_by_role("tab", name="Members").click()
    expect(lead.get_by_role("listitem").filter(has_text="lead@example.org")).not_to_contain_text("Suspend")
    lead.get_by_label("Search members by name or email").fill("ERIN")
    erin_row = lead.get_by_role("listitem").filter(has_text="erin@example.org")
    expect(lead.get_by_role("list", name="Members").get_by_role("listitem")).to_have_count(1)
    erin_row.get_by_role("button", name="Make admin").click()
    lead.get_by_role("dialog").get_by_role("button", name="Make admin").click()
    expect(erin_row).to_contain_text("Admin")
    erin_row.get_by_role("button", name="Remove admin").click()
    lead.get_by_role("dialog").get_by_role("button", name="Remove admin").click()
    expect(erin_row.get_by_role("button", name="Make admin")).to_be_visible()
    erin.reload()
    expect(erin).to_have_url(f"{base}/workspace")
    erin.wait_for_load_state("networkidle")
    erin_row.get_by_role("button", name="Suspend").click()
    lead.get_by_role("dialog").get_by_role("button", name="Suspend").click()
    expect(erin_row).to_contain_text("Suspended")
    erin.get_by_label("Workspace year").select_option("2023")
    expect(erin.get_by_text("Your session ended. Sign in again to continue.")).to_be_visible()
    erin_row.get_by_role("button", name="Restore").click()
    lead.get_by_role("dialog").get_by_role("button", name="Restore").click()
    expect(erin_row).to_contain_text("Active")

    # Audit: every change above, by whom and to whom.
    lead.get_by_role("tab", name="Audit").click()
    history = lead.get_by_role("list", name="Audit history")
    expect(history).to_contain_text("lead@example.org approved the request of erin@example.org")
    expect(history).to_contain_text("lead@example.org revoked the invitation for gina@example.org")
    expect(history).to_contain_text("lead@example.org changed the account of erin@example.org (role member, suspended)")

    # A member cannot open the console or call its API.
    sign_in(erin, "erin@example.org")
    erin.goto("/admin")
    expect(erin).to_have_url(f"{base}/workspace")
    assert erin.request.get("/api/admin/users").status == 403
    for context in (lead_context, erin_context):
        context.close()


@pytest.mark.parametrize("path,code", [
    ("/auth/callback?error=access_denied&state=x", "identity_provider_error"),
    ("/auth/callback?state=x&code=y", "invalid_login_flow"),
    ("/auth/login?return_to=//attacker.test", "invalid_return_path"),
])
def test_sign_in_problems_land_on_the_explanation_page(browser, base, path, code):
    context, page = person(browser, base)
    page.goto(path)
    expect(page).to_have_url(f"{base}/sign-in-error?code={code}")
    expect(page.get_by_role("heading", name="Sign-in did not complete")).to_be_visible()
    expect(page.get_by_text("contact a Wildfire administrator", exact=False)).to_be_visible()
    expect(page.locator("code")).to_have_text(code)
    context.close()


def test_pending_account_moves_on_without_reload_and_invitation_opens_while_signed_in(browser, base):
    ops_context, ops = person(browser, base)
    sign_in(ops, "ops@example.org")
    assert ops.request.post("/dev-idp/bootstrap-admin", form={"email": "ops@example.org"}).ok

    def apply(page, email):
        page.goto("/")
        page.get_by_role("button", name="Request access").first.click()
        page.get_by_role("link", name="Create an account").click()
        identity_page(page, email)
        page.get_by_label("Full name").fill(email.split("@")[0].title())
        page.get_by_label("Organization").fill("Example Lab")
        page.get_by_label("How you plan to use the platform").fill("Testing.")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_role("heading", name="Your request is under review")).to_be_visible()

    # Approval reaches a waiting page when it comes back into view.
    pat_context, pat = person(browser, base)
    apply(pat, "pat@example.org")
    pat_id = account(pat)["user"]["id"]
    pending = ops.request.get("/api/admin/access-requests?status=pending").json()["items"]
    request_id = next(row["id"] for row in pending if row["applicant_id"] == pat_id)
    write(ops, base, "POST", f"/api/admin/access-requests/{request_id}/decision", {"decision": "approve"})
    pat.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(pat).to_have_url(f"{base}/workspace")

    # An invitation opened by someone already signed in (here, still pending) is claimed, not misreported.
    quinn_context, quinn = person(browser, base)
    apply(quinn, "quinn@example.org")
    write(ops, base, "POST", "/api/admin/invitations", {"email": "quinn@example.org"})
    quinn.goto(mail_to(ops, "quinn@example.org")[-1]["link"])
    identity_page(quinn, "quinn@example.org")
    expect(quinn).to_have_url(f"{base}/workspace")
    for context in (ops_context, pat_context, quinn_context):
        context.close()
