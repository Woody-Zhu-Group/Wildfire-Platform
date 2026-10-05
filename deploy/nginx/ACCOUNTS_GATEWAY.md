# Accounts gateway rollout

These are staged templates, not the live origin configuration.
Do not enable them while the website uses anonymous cross-origin API calls.

First configure real Cognito/MFA, verified initial administrator IDs, SES and the
restricted accounts DB role. Serve the website's accounts build
(`npm run build:accounts`: landing, sign-in dialog, status, invitation and
administrator console) from the same HTTPS origin as /auth/* and /api/*. Page
paths (`/workspace`, `/access-status`, `/invite`, `/admin`) must load
`index.html`; rewrite them in a CloudFront Function on the site behavior, not
with custom error pages, which would also turn API 404s into the page. Rehearse
with `tests/accounts/staging_harness.py` (services/accounts/README.md).

Confirm `nginx -V` includes http_auth_request_module. Bind business ports
to loopback/private interfaces or equivalent security-group restrictions.
No old public port or CloudFront origin may bypass the gateway.

Copy accounts-proxy.conf and accounts-protected.conf to /etc/nginx/wildfire/.
Include accounts-gateway.conf as a server under Nginx's http context. It listens
on loopback 8080; the existing trusted HTTPS origin forwards original /auth/*
and /api/* prefixes to it. Do not nest its server inside another server.

CloudFront must forward flow/session cookies, Origin, X-CSRF-Token, needed
methods and query strings, and return Set-Cookie. Remove legacy prefix-stripping
functions before this gateway strips a prefix. Use caching-disabled behavior
with minimum TTL zero for auth and private API/SSE. Origin no-store does not
override a nonzero minimum TTL. Public static assets use a separate behavior
without session cookies.

Do not log cookies, OAuth codes/state, invite tokens or authorization headers.
The private template disables request/error logging; use stable account error
codes and DB audit. Apply equivalent no-sensitive-fields configuration to the
outer TLS origin and CloudFront logs. Do not enable HTTPX/botocore payload debug.

The internal subrequest validates session/current membership before dispatch.
Original method is saved only in the protected location: POST must not become a
CSRF-exempt GET. Accounts checks Origin/CSRF for unsafe original methods.
The gateway overwrites caller identity headers and strips Cookie/Authorization
before business forwarding. Account/admin routes enforce their own roles.

Agent streaming keeps buffering off and a 300-second read timeout; other
business routes use 90 seconds and account endpoints 30 seconds. Account
suspension does not retroactively cancel admitted streams; later frontend
logout must cancel its own requests.

Before rollout, validate nginx -t and loopback account health, then verify guest
and pending denial on EVERY prefix, member admin rejection, identity isolation,
valid/invalid POST CSRF, stream disconnection, Cookie/Set-Cookie forwarding,
no CDN cache reuse, direct-port/internal-path rejection, expiry and role changes.
Roll back only gateway routing if acceptance fails; retain user/audit data.
