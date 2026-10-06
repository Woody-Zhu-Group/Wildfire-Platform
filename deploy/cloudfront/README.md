# Accounts origin on CloudFront

Templates and workflows for serving the signed-in website (the website's
accounts build) and the accounts gateway from one HTTPS origin. Nothing here
changes the live distribution or the GitHub Pages site until an operator applies
it; prerequisites for the gateway itself are in `deploy/nginx/ACCOUNTS_GATEWAY.md`.

| File | Use |
|---|---|
| `site-rewrite.js` | CloudFront Function: page paths load `/index.html` |
| `security-headers.json` | Response headers policy: CSP, HSTS, no framing, nosniff, referrer and permissions policies |
| `check-csp.mjs` | Fails when a built page has an inline script the CSP does not allow |
| `iam/github-deploy-trust.json`, `iam/github-deploy-permissions.json` | Role for the deploy workflow (GitHub OIDC, no stored keys) |
| `../pages-redirect/index.html` | Cutover page for the old GitHub Pages address |
| `.github/workflows/deploy-accounts-site.yml` | Manual deploy: test, build, check the CSP, upload to S3, invalidate |
| `.github/workflows/pages-redirect.yml` | Manual cutover of GitHub Pages to the redirect page |

## Distribution layout

| Behavior | Origin | Cache policy | Origin request policy | Function | Response headers |
|---|---|---|---|---|---|
| Default (`*`) | Site bucket through origin access control | Managed-CachingOptimized | None | `site-rewrite.js`, viewer request | `wildfire-security-headers` |
| `/auth/*` | EC2 HTTPS origin, which forwards to the Nginx gateway | Managed-CachingDisabled | Managed-AllViewerExceptHostHeader | None | `wildfire-security-headers` |
| `/api/*` | Same | Managed-CachingDisabled | Managed-AllViewerExceptHostHeader | None | `wildfire-security-headers` |

- `/auth/*` and `/api/*` allow GET, HEAD, OPTIONS, PUT, POST, PATCH and DELETE, and forward every cookie, query string and header except Host. That is how the session cookie, `Origin` and `X-CSRF-Token` reach the gateway, and how `Set-Cookie` comes back.
- Keep the origin response timeout that the current `/api/agent` behavior uses for the Ask stream.
- Remove any legacy function that strips an `/api` prefix before the gateway does (`ACCOUNTS_GATEWAY.md`).
- The rewrite runs only on the site behavior, so an API 404 stays a 404. Do not use custom error pages for page paths, because they would also turn API errors into the page.

## Site bucket

A private bucket with Block Public Access on, read only by the distribution
through origin access control. The bucket policy, with your values filled in:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "cloudfront.amazonaws.com" },
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::<SITE_BUCKET>/*",
    "Condition": { "StringEquals": { "AWS:SourceArn": "arn:aws:cloudfront::<ACCOUNT_ID>:distribution/<DISTRIBUTION_ID>" } }
  }]
}
```

The deploy workflow uploads `assets/*` (content-hashed, cached for a year)
before `index.html` (`no-cache`), then invalidates `/index.html` only. The
function rewrites page paths before the cache, so one invalidation covers every
page path. Old hashed files are kept for pages that are still open.

## Apply the function and the headers policy

```sh
aws cloudfront create-function --name wildfire-site-rewrite \
  --function-config '{"Comment":"Page paths load index.html","Runtime":"cloudfront-js-2.0"}' \
  --function-code fileb://deploy/cloudfront/site-rewrite.js
aws cloudfront describe-function --name wildfire-site-rewrite --query ETag --output text
aws cloudfront publish-function --name wildfire-site-rewrite --if-match <ETAG>
aws cloudfront create-response-headers-policy --response-headers-policy-config file://deploy/cloudfront/security-headers.json
```

Then attach the function to the default behavior (viewer request) and the
headers policy to all three behaviors. After a change to either file, update the
function (`update-function`, then `publish-function`) or the policy
(`update-response-headers-policy`) in the same way.

The CSP allows the inline theme script in `website/index.html` by its SHA-256
hash. `.gitattributes` keeps that file's line endings LF on every checkout, so
the hash does not depend on the machine. If the script changes, the website test
(`tests/cloudfront.test.ts`), CI and the deploy workflow all fail until the new
hash is added to `script-src`. The only outside source allowed is the
OpenStreetMap tile server for maps; the test checks that every tile host in
`website/src` is in `img-src`. The staging harness sends this exact policy, so
the browser tests run under it and fail on any violation.

## Deploy role and environments

1. If the account has no IAM OIDC provider for `token.actions.githubusercontent.com`, create one with audience `sts.amazonaws.com`.
2. Create a role per environment. Use `iam/github-deploy-trust.json` as the trust policy (replace `<ACCOUNT_ID>` and `<ENVIRONMENT>`) and `iam/github-deploy-permissions.json` as its only permissions (replace `<SITE_BUCKET>`, `<ACCOUNT_ID>`, `<DISTRIBUTION_ID>`). The role can write site files and invalidate one distribution, nothing else.
3. In the GitHub repository, create environments such as `staging` and `production`, each with these variables (not secrets): `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`, `SITE_BUCKET`, `CLOUDFRONT_DISTRIBUTION_ID`. Required reviewers on `production` are recommended.
4. Actions, "Deploy accounts site", Run workflow, pick the environment. `production` deploys only from `main`.

## Cutover

1. Finish the gateway prerequisites in `ACCOUNTS_GATEWAY.md`: Cognito, SES out of the sandbox, the accounts migration and runtime role, the accounts service, the gateway, and business ports closed to everything but the gateway.
2. Set up the bucket, behaviors, function and headers policy above. Deploy to `staging`, then walk the acceptance list in `ACCOUNTS_GATEWAY.md`.
3. Deploy to `production`.
4. An administrator switches the repository's Settings, Pages, Source from "Deploy from a branch" (`main`, `/docs`) to "GitHub Actions", then runs "Pages redirect" with the production origin. The old Pages address then sends visitors to the signed-in site. `docs/` and its build-freshness test stay as they are until the team retires the Pages build.

Rollback: switch the Pages source back to `main`, `/docs`; to undo a site deploy,
run the deploy workflow on the previous commit. Account and audit data are not
touched by either.
