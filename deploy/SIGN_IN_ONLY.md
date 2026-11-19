# Sign-in-only deployment on one server

Status: not in service anywhere yet. Deploy it from `main`.

One Ubuntu server outside AWS runs the signed-in website, the accounts service,
its PostgreSQL database and the Nginx gateway. Requests the gateway admits are
relayed to the existing public API origin, so the business services, the
warehouse and the agent stay where they are on AWS. Nothing on AWS changes, and
no AWS access is needed.

```
browser ── HTTPS ──> Nginx :443                       deploy/nginx/accounts-site.conf
                      ├─ /                            website/dist/accounts (npm run build:accounts)
                      └─ /auth/*, /api/* ──> gateway 127.0.0.1:8080   accounts-gateway.conf
                            ├─ account routes ──> accounts service 127.0.0.1:8005 ──> PostgreSQL 127.0.0.1:5432
                            └─ /api/{agent,data-query,visualization,risk-forecasting}/*
                                  only for active members, Cookie and Authorization removed
                                  ──> relay 127.0.0.1:8000-8004            accounts-relay.conf
                                  ──> verified HTTPS ──> d3t70p3if3twy3.cloudfront.net/api/*
```

## What it does not do

- **The public API origin and the GitHub Pages site stay open to anyone.** The
  repository is public and names the API origin, and the services allow any web
  origin. Signing in restricts this website, not the data or the agent behind
  it. Closing the old entry needs AWS access (`deploy/nginx/ACCOUNTS_GATEWAY.md`,
  `docs/DEPLOY_RUNBOOK.md` section 6) and is a separate decision.
- Two steps reduce the exposure without AWS: switch the old Pages address to the
  redirect page (`deploy/cloudfront/README.md`, Cutover step 4; needs a
  repository administrator), and set a monthly spend limit on the OpenRouter key
  the production agent uses.
- The business services do not read the verified user id, so there is no
  per-user usage record. The login rate limit is per process, as described in
  `services/accounts/README.md`.

## What you need

| Item | Requirements |
|---|---|
| Server | Ubuntu 24.04, public IPv4, 2 GB memory is enough, SSH key login. |
| Domain | DNS you control. An `A` record (and `AAAA` if the server has IPv6) for the host name, pointing at the server. On Cloudflare, set it to DNS only, so TLS ends on this server. |
| Identity provider | Any OpenID Connect provider that can require MFA (Auth0, Zitadel, Keycloak, Cognito and others). Authorization code flow with PKCE S256; RS256-signed ID tokens carrying `email` and a boolean `email_verified`; callback URL `https://HOST/auth/callback`; client authentication by HTTP Basic (`client_secret_basic`), or a public client with no secret. Require MFA for every user. |
| Mail | An SMTP service with a verified sender domain (its SPF and DKIM records added to DNS). Port 587 with STARTTLS or port 465 with TLS; the service refuses unencrypted SMTP. |
| Agreement | The team lead agrees that applicants' names, email addresses, organizations and stated purposes are stored on this server and with the identity and mail providers. |

Choose the identity provider you mean to keep. Accounts are keyed by issuer and
subject, so moving to another provider later (Cognito included) makes every
person a new, pending account that has to be approved or invited again.

## 1. Server

Commands that start with `sudo` need an administrator. If the image has only
`root`, create the `ubuntu` user the systemd units expect (`adduser ubuntu`,
`usermod -aG sudo ubuntu`, then copy your SSH key to `/home/ubuntu/.ssh`).

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y nginx postgresql python3-venv git certbot ufw
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
nginx -V 2>&1 | grep -o http_auth_request_module   # must print the module name
grep '^nameserver' /etc/resolv.conf                # expect 127.0.0.53
```

Once `ubuntu` can sign in with the key and use `sudo`, turn off root and password
logins. Keep the current session open and check that a new `ubuntu` session
still connects before closing it:

```bash
printf 'PermitRootLogin no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/10-hardening.conf
sudo sshd -t && sudo systemctl restart ssh
```

The relay looks up the API origin through `127.0.0.53` (systemd-resolved). If
`/etc/resolv.conf` names another server, put that address in the `resolver`
line of `/etc/nginx/wildfire/accounts-relay-upstream.conf` in step 7.

## 2. Code

As `ubuntu`. The directory name matches `deploy/systemd/`.

```bash
cd /home/ubuntu
git clone https://github.com/Woody-Zhu-Group/Wildfire-Platform.git Wildfire-Services
cd Wildfire-Services
python3 -m venv .venv
.venv/bin/pip install -r services/accounts/requirements.txt
```

## 3. Database

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE accounts_owner LOGIN;
CREATE ROLE accounts_runtime LOGIN;
CREATE DATABASE accounts OWNER accounts_owner;
REVOKE ALL ON DATABASE accounts FROM PUBLIC;
GRANT CONNECT ON DATABASE accounts TO accounts_runtime;
\q
```

`accounts_owner` applies migrations; the service runs as `accounts_runtime`,
which cannot create anything, and refuses to start under a privileged role.
PostgreSQL on Ubuntu listens on `127.0.0.1` only.

The roles get passwords when they are first used: the runtime password in step
4 and a throwaway owner password in step 5. Neither needs keeping anywhere
else, because `sudo -u postgres psql` can set a new one at any time. Make each
with `openssl rand -hex 24`; hex keeps the connection URL and systemd's
environment file simple. Run `clear` afterwards so the password does not stay
on screen.

## 4. Settings

```bash
openssl rand -hex 24                                    # the runtime password
sudo -u postgres psql -c '\password accounts_runtime'   # paste it twice
cd /home/ubuntu/Wildfire-Services
install -m 600 /dev/null .env
nano .env                                               # paste it into ACCOUNTS_DATABASE_URL
clear
```

Open `<issuer>/.well-known/openid-configuration` in a browser and copy
`issuer`, `authorization_endpoint`, `token_endpoint` and `jwks_uri` exactly.
Keep a trailing `/` on the issuer if the provider publishes one; ID tokens must
match it character for character.

```
ACCOUNTS_DATABASE_URL=postgresql://accounts_runtime:RUNTIME_PASSWORD@127.0.0.1:5432/accounts
ACCOUNTS_SECRET=OUTPUT_OF_openssl_rand_-hex_32
ACCOUNTS_PUBLIC_ORIGIN=https://accounts.example.org
ACCOUNTS_OIDC_ISSUER=ISSUER
ACCOUNTS_OIDC_AUTHORIZATION_URL=AUTHORIZATION_ENDPOINT
ACCOUNTS_OIDC_TOKEN_URL=TOKEN_ENDPOINT
ACCOUNTS_OIDC_JWKS_URL=JWKS_URI
ACCOUNTS_OIDC_CLIENT_ID=CLIENT_ID
ACCOUNTS_OIDC_CLIENT_SECRET=CLIENT_SECRET
ACCOUNTS_MAIL_FROM=no-reply@example.org
ACCOUNTS_MAIL_TRANSPORT=smtp
ACCOUNTS_SMTP_HOST=SMTP_HOST
ACCOUNTS_SMTP_PORT=587
ACCOUNTS_SMTP_USERNAME=SMTP_USERNAME
ACCOUNTS_SMTP_PASSWORD=SMTP_PASSWORD
```

For Cognito, set `ACCOUNTS_OIDC_DOMAIN` to the hosted UI domain instead of the
three endpoint URLs. The owner has no password in this file or anywhere else
(step 5).
systemd reads the file strictly; see `deploy/systemd/SYSTEMD_SETUP.md`
(EnvironmentFile).

## 5. Migrate, grant, check the provider

```bash
cd /home/ubuntu/Wildfire-Services
export PYTHONPATH=$PWD
openssl rand -hex 24                                    # a throwaway owner password
sudo -u postgres psql -c '\password accounts_owner'     # paste it twice
read -rs -p "accounts_owner password: " OWNER; echo     # and once more here
export ACCOUNTS_MIGRATION_DATABASE_URL="postgresql://accounts_owner:$OWNER@127.0.0.1:5432/accounts"
.venv/bin/python -m services.accounts.cli migrate
.venv/bin/python -m services.accounts.cli grant-runtime --role accounts_runtime
unset OWNER ACCOUNTS_MIGRATION_DATABASE_URL
sudo -u postgres psql -c 'ALTER ROLE accounts_owner PASSWORD NULL'
clear
.venv/bin/python -m services.accounts.cli check-oidc
```

Removing the owner's password afterwards means nothing can sign in to the
database as the owner until the next migration, which repeats these lines.

`check-oidc` compares the configured issuer and endpoints with the provider's
discovery document and confirms RS256, PKCE S256 and, with a client secret,
HTTP Basic client authentication. Every line must start with `ok`.

## 6. Accounts service

```bash
sudo cp deploy/systemd/wildfire-accounts.service deploy/systemd/wildfire-accounts-prune.service \
  deploy/systemd/wildfire-accounts-prune.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wildfire-accounts wildfire-accounts-prune.timer
curl -s http://127.0.0.1:8005/health   # {"status":"ok","service":"accounts"}
journalctl -u wildfire-accounts -n 30 --no-pager
```

## 7. Nginx and the certificate

```bash
cd /home/ubuntu/Wildfire-Services
HOST=accounts.example.org
sudo mkdir -p /etc/nginx/wildfire /var/www/letsencrypt /var/www/wildfire
sudo cp deploy/nginx/accounts-proxy.conf deploy/nginx/accounts-protected.conf \
  deploy/nginx/accounts-relay-upstream.conf /etc/nginx/wildfire/
python3 deploy/nginx/security_headers.py | sudo tee /etc/nginx/wildfire/security-headers.conf >/dev/null
sudo cp deploy/nginx/accounts-gateway.conf deploy/nginx/accounts-relay.conf /etc/nginx/conf.d/
sed "s/accounts.example.org/$HOST/g" deploy/nginx/accounts-site-http.conf | sudo tee /etc/nginx/sites-available/wildfire-http >/dev/null
sudo ln -s /etc/nginx/sites-available/wildfire-http /etc/nginx/sites-enabled/wildfire-http
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Once the DNS record resolves to the server:

```bash
sudo certbot certonly --webroot -w /var/www/letsencrypt -d "$HOST" --deploy-hook "systemctl reload nginx"
sed "s/accounts.example.org/$HOST/g" deploy/nginx/accounts-site.conf | sudo tee /etc/nginx/sites-available/wildfire >/dev/null
sudo ln -s /etc/nginx/sites-available/wildfire /etc/nginx/sites-enabled/wildfire
sudo nginx -t && sudo systemctl reload nginx
```

certbot renews through the port 80 server and reloads Nginx afterwards.

## 8. Website

Build on your own computer (Node 24, as in CI) and copy it up:

```bash
cd website
npm ci
npm run build:accounts
node ../deploy/cloudfront/check-csp.mjs dist/accounts/index.html
scp -r dist/accounts ubuntu@SERVER:/tmp/wildfire-site
```

On the server:

```bash
sudo cp -r /tmp/wildfire-site/. /var/www/wildfire/
rm -rf /tmp/wildfire-site
```

Old files in `assets/` can stay; pages that are already open still use them.

## 9. First administrator

Open `https://HOST`, sign in (the provider asks for MFA) and you land on the
access status page as a pending account. Then, on the server:

```bash
sudo -u postgres psql accounts -c "SELECT id, email, status, created_at FROM app.users ORDER BY created_at"
cd /home/ubuntu/Wildfire-Services
PYTHONPATH=$PWD .venv/bin/python -m services.accounts.cli bootstrap-admin --user-id USER_ID
```

Later administrators are appointed in the console at `/admin`.

## 10. Acceptance

From your own computer:

```bash
HOST=accounts.example.org
for p in agent data-query visualization risk-forecasting; do
  printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "https://$HOST/api/$p/health"
done                                                     # every line 401
curl -sI "https://$HOST/" | grep -iE 'content-security-policy|strict-transport-security|x-frame-options'
```

On the server, every internal port must be bound to loopback only:

```bash
sudo ss -ltnp | grep -E ':(5432|8000|8001|8002|8004|8005|8080) '   # all 127.0.0.1
```

In a browser: sign in with MFA; apply; approve in `/admin` and receive the mail;
as the member, load the map and records and ask a question (the answer streams
through the relay); invite an address and accept it, then try the link with a
different account; suspend the member and confirm the next request is refused;
sign out. `/var/log/nginx/wildfire-relay.error.log` should stay empty.

## Backups

The database holds users, applications, invitations and the audit history. Turn
on the provider's server snapshots, and keep a dump off the server:

```bash
sudo -u postgres pg_dump --format=custom accounts > accounts-$(date +%F).dump
```

## Updates

```bash
cd /home/ubuntu/Wildfire-Services
git pull --ff-only
.venv/bin/pip install -r services/accounts/requirements.txt
sudo systemctl restart wildfire-accounts
```

If `db/migrations/accounts/` changed, run `migrate` as in step 5 before the
restart. If `deploy/nginx/` changed, copy the changed files as in step 7, then
`sudo nginx -t && sudo systemctl reload nginx`. If `website/` changed, rebuild
and copy it as in step 8.

## Rollback

`sudo rm /etc/nginx/sites-enabled/wildfire && sudo systemctl reload nginx` takes
the site down; `sudo systemctl stop wildfire-accounts` stops the service. Users,
applications and audit history stay in the database.

## Moving to the AWS layout later

When the business services run behind the gateway on the AWS host, remove
`accounts-relay.conf` there (its servers would take the business ports) and
follow `deploy/nginx/ACCOUNTS_GATEWAY.md`. Move the accounts data with
`pg_dump` and `pg_restore` into the accounts database on RDS, and keep the same
identity provider so every account keeps its identity.
