# Accounts access foundation

This branch adds an independent FastAPI accounts service on loopback port 8005.
The website's accounts build (`npm run build:accounts`, `website/src/access/`)
is its frontend, including the administrator console at `/admin`; neither it nor
the gateway has been deployed, and the GitHub Pages site stays anonymous.
Cognito handles passwords/MFA; application membership, invitations, approval,
sessions and audit history live in PostgreSQL's `app` schema.
Jev and the existing business services are unchanged.

## Provisioning

Install repository requirements and configure the root `ACCOUNTS_` environment
template. Runtime needs a dedicated non-owner database role, with no superuser,
role-creation, database-creation or `app` schema-creation privileges.
Startup rejects privileged connections. Migration credentials are separate and
never inferred from the warehouse connection.

```sh
python -m services.accounts.cli migrate
python -m services.accounts.cli grant-runtime --role accounts_runtime
uvicorn services.accounts.app:app --host 127.0.0.1 --port 8005 --no-access-log
```

The first two commands use `ACCOUNTS_MIGRATION_DATABASE_URL`. An operator
creates the runtime role beforehand; these commands do not generate or echo
credentials. Runtime can SELECT/INSERT/UPDATE users, requests and invitations;
expired sessions/flows may also be deleted. Audit permits only SELECT/INSERT.
Warehouse loaders must not receive app privileges; migrations do not touch
warehouse schemas or tables.

Initial administrators first sign in with verified Cognito identities, becoming
pending members. A trusted operator selects each verified user UUID:

```sh
python -m services.accounts.cli bootstrap-admin --user-id VERIFIED_USER_UUID
```

Bootstrap is audited. There is no public bootstrap API, first-user admin rule,
email-domain admin rule or production test-login flag. Later administrator
assignment uses the authenticated management API after membership approval.

Configure the Cognito pool/federation policy to enforce MFA. JWT signature
verification is not an MFA-policy check. Real Cognito, MFA, mail and HTTPS/CDN
acceptance require configuration; local tests do not establish those conditions.

## Identity and session

Login issues a short-lived encrypted transaction and uses authorization code,
PKCE S256, state and nonce. Callback consumes it only when its browser cookie
matches, exchanges the code, and validates RS256 ID-token signatures against the
configured issuer's JWKS. Issuer, audience, expiry, issued-at, nonce, subject,
`token_use=id` and a boolean verified-email claim are required.
Identity tokens cannot supply application roles.

Identity is keyed by issuer + subject. Email matches do not automatically merge
users. New identities are pending unless already assigned membership.

The browser receives only random opaque session IDs. PostgreSQL stores their
SHA-256 digests. Cookies use `__Host-`, Secure, HttpOnly, SameSite=Lax and
Path=/ with no Domain. Sessions expire after 12 hours or 30 idle minutes.
Successful login revokes the previous browser session. Login/PKCE payloads are
Fernet-encrypted with a purpose-derived server key and expire after ten minutes.

Each request reads current session and user state. Pending/suspended accounts
cannot pass the business gateway. Cookie writes require exact Origin and
session-bound CSRF from `/api/auth/me`. Logout revokes the current session;
suspension revokes every target session. Expired cookies can still be logged out
with their prior CSRF value.

Authentication occurs at request admission. Suspending an account does not
retroactively interrupt an admitted business operation or SSE stream. Frontend
logout must cancel its own requests. Strict mid-stream revocation needs further
execution integration.

## Applications and invitations

Roles: member/admin. Membership: pending/active/suspended.
Applications: pending/approved/rejected. A partial unique index allows one
pending application per user; rejected applicants may reapply. Applicants never
receive private review notes.

Approval/rejection updates the locked request, qualification and audit in one
transaction. Privileged mutations serialize and reread current administrator
status inside the transaction. The last active administrator is protected.
Pending users cannot bypass review through ordinary account updates.

Invitations grant ordinary membership to the specified verified email, lasting
seven days. Opening a link does not consume it. Claim establishes an encrypted,
short-lived login binding; accept verifies email, expiry, status and the current
token digest before a transaction. Resend invalidates earlier tokens AND
already-started flows/sessions. Used/revoked/expired/wrong-email invites fail,
and invitations cannot restore a suspended account. Pending applications resolved
by invitation are audited.

## Notifications

Notifications follow the observer pattern (`notify.py`). Routes publish an event
after their transaction commits; observers subscribed at startup decide who hears
about it and how. Today every observer sends SES mail:

| Event | Published by | Observer |
|---|---|---|
| `AccessRequested` | `POST /api/access-requests`, after the response | Mails every active administrator: who asked, organization, intended use, a link to `/admin` |
| `AccessReviewed` | A decision and `/notify` | Mails the applicant the result and the public note |
| `InvitationIssued` | Creating and resending an invitation | Mails the invitee the link with the token |

A failing observer never undoes the event or stops the other observers. Review
and invitation mail record their delivery (`notification_status`,
`delivery_status`) for the console; a failed administrator alert is logged by
kind only, and the request still appears in the console. Resend creates a new
invitation token; `/notify` retries only the stored review recipient/public
note. Secrets never enter audit metadata, logs or API errors. A new channel is
one more `subscribe` call in `build_notifier`.

Public login/claim limits are process-local: 100 requests per five minutes per
transport peer, with bounded key storage. Behind loopback proxying that peer is
shared. Production needs viewer-level edge protection. This is not a distributed
limiter or a per-user paid-Agent quota.

## API

Responses are private/no-store. Redirects use 302; payloads are JSON;
logout/revoke/internal authorization use 204. `/auth/login` and `/auth/callback`
are browser navigations, so they never answer with JSON: any failure (expired or
replayed flow, the identity provider returning an error, a rejected identity,
the flow limit, an unsupported return path, the database being unavailable)
redirects to `/sign-in-error?code=<error code>`, which shows the code and asks
the person to contact an administrator.

| Method | Path | Access |
|---|---|---|
| GET | /auth/login | Public, bounded return path |
| GET | /auth/callback | Browser-bound state |
| GET | /api/auth/me | Valid session, including pending |
| POST | /auth/logout | Exact Origin, CSRF when cookie exists |
| POST | /api/access-requests | Verified pending user, CSRF |
| GET | /api/access-requests/me | Own request |
| POST | /api/invitations/claim | Public, exact Origin, rate limit |
| POST | /api/invitations/accept | Bound verified identity, CSRF |
| GET | /api/admin/access-requests | Active admin |
| POST | /api/admin/access-requests/{id}/decision | Active admin, CSRF |
| POST | /api/admin/access-requests/{id}/notify | Active admin, CSRF |
| GET/POST | /api/admin/invitations | Active admin, CSRF for writes |
| POST | /api/admin/invitations/{id}/resend | Active admin, CSRF |
| POST | /api/admin/invitations/{id}/revoke | Active admin, CSRF |
| GET | /api/admin/users | Active admin; `status` and `q` (name or email, literal text) filters |
| PATCH | /api/admin/users/{id} | Active admin, CSRF |
| GET | /api/admin/audit-events | Active admin |
| GET | /internal/auth/active | Loopback, trusted original method |

Lists use limit 1..100 and UUID cursors in descending creation order. Each row
carries the people its ids point to: requests add `applicant_name`,
`applicant_email`, `applicant_status` and `reviewer_email`; invitations add
`invited_by_email` and `accepted_by_email`; audit events add `actor_email` and
`target_email`. Invitation token hashes never leave the service.
Errors have `{"error":{"code":"...","message":"..."}}` shape without submitted
credentials. 401: bad session; 403: privilege/CSRF; 409: conflict; 410: unavailable
invitation; 422: input; 429: flow limit. DB/provider faults fail closed.
Health checks the DB, not the full deployment.

No private-static-resource delivery, conversation memory, cloud workspace
persistence or per-user Agent budget is added here.

## Verification

Set `ACCOUNTS_TEST_DATABASE_URL` to a dedicated loopback database named
`accounts_test_*`. Tests migrate it, create restricted runtime roles and
reset its app data. Do not point this command at real users:

```sh
pytest tests/accounts/test_accounts.py tests/accounts/test_identity.py
ruff check services/accounts tests/accounts
```

Tests use real PostgreSQL and RSA signatures, with mocked OIDC HTTP/mail.
No paid Agent/Jev calls run. Cases include concurrent review, last-admin races,
audit-failure rollback, DB privileges, applicant isolation, expiry/revocation,
browser-state binding, cookie flags, CSRF and invitation replay/resend.

For the real Nginx admission contract, start the included Linux harness in an
owned temporary directory:

```sh
python3 tests/accounts/gateway_harness.py --nginx /path/to/nginx --directory /tmp/TASK/nginx
ACCOUNTS_GATEWAY_TEST_URL=http://127.0.0.1:18080 pytest tests/accounts/test_gateway.py
```

It uses loopback 18004/18005/18080 and protocol stubs, not a real IdP or Agent.
Checks cover four service prefixes, original-method CSRF, denial before dispatch,
identity-header replacement, credential stripping, no-store and internal paths.
Without an explicit harness URL these optional tests are skipped. Stop with
SIGTERM and remove only the owned temporary directory.

To rehearse the whole signed-in site, run the local staging harness on Linux
(WSL works). It serves `website/dist/accounts` on https://localhost:8443 (a
self-signed stand-in for CloudFront) and forwards `/auth/*` and `/api/*` to the
real `deploy/nginx` gateway, which admits requests through this service on a
throwaway PostgreSQL. A development sign-in page stands in for Cognito, mail is
listed at `/dev-idp/mail`, the data APIs are read-only relays to the public
services, and the agent is a stub that calls no model:

```sh
(cd website && npm run build:accounts)
PYTHONPATH=<linux site-packages>:. python3 tests/accounts/staging_harness.py \
  --nginx /path/to/nginx --postgres-bin /path/to/postgresql/16/bin \
  --site website/dist/accounts --directory /tmp/TASK/staging
ACCOUNTS_STAGING_URL=https://localhost:8443 pytest tests/accounts/test_staging_flow.py
```

The browser test needs Python Playwright with Chromium and a fresh harness. It
walks sign-in, applying, review in the console, live data under the verified
user id, Ask with and without CSRF, invitations for the right and wrong
address, member changes, suspension, the audit history and sign-out. Without
`ACCOUNTS_STAGING_URL` it is skipped.

Local acceptance uses PostgreSQL 16.15 and Nginx 1.24.0 from maintained Ubuntu
packages. Production provider/CDN/network acceptance remains required.
