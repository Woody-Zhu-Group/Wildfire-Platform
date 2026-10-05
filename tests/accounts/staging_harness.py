"""Local staging of the signed-in website on Linux (WSL), never production.

One HTTPS origin (https://localhost:8443) stands in for CloudFront: it serves the
accounts build of the website and forwards /auth/* and /api/* to the real
deploy/nginx gateway, which admits requests through the real accounts service
on a throwaway PostgreSQL. A development identity page stands in for Cognito,
mail goes to /dev-idp/mail, the data APIs are read-only relays to the public
services, and the agent is a stub that never calls a model.

    PYTHONPATH=<linux site-packages>:<repo> python3 tests/accounts/staging_harness.py \
        --nginx <nginx> --postgres-bin <postgresql/16/bin> --site website/dist/accounts \
        --directory /tmp/<owned new directory>

Requires `npm run build:accounts` in website/ first. Stop with Ctrl+C or SIGTERM;
the caller removes the directory afterwards.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import shutil
import signal
import subprocess
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import parse_qs, urlencode, urlsplit

import psycopg
import uvicorn

from services.accounts.app import create_app
from services.accounts.cli import MIGRATIONS, grant_runtime
from services.accounts.config import Settings
from services.accounts.identity import OIDC, Identity
from services.accounts.security import token
from services.accounts.store import Store

ORIGIN = "https://localhost:8443"
ISSUER = "https://dev-identity.invalid"
PG_PORT = 55439
PUBLIC_API = "https://d3t70p3if3twy3.cloudfront.net/api/"
RELAYS = {18000: "data-query", 18001: "risk-forecasting", 18002: "visualization"}
MAIL: list[dict] = []
SEEN: dict[str, str | None] = {}


class DevIdentity:
    """Signs in whoever the development page names. The code carries that identity."""

    def __init__(self, settings: Settings):
        self.authorize_url = OIDC(settings, None).authorize_url

    async def exchange(self, code: str, verifier: str, nonce: str) -> Identity:
        person = json.loads(base64.urlsafe_b64decode(code.encode()))
        return Identity(ISSUER, "dev:" + person["email"], person["email"], person["name"])


class DevMailer:
    """Keeps messages for /dev-idp/mail instead of sending them; same text as mail.py."""

    def invite(self, email: str, raw: str) -> None:
        MAIL.append({"to": email, "kind": "invitation", "link": ORIGIN + "/invite#token=" + raw})

    def access_requested(self, email: str, event) -> None:
        MAIL.append({"to": email, "kind": "access_requested", "applicant": event.email, "link": ORIGIN + "/admin"})

    def review(self, email: str, status: str, note: str) -> None:
        MAIL.append({"to": email, "kind": "review", "status": status, "note": note, "link": ORIGIN + "/access-status"})


class Quiet(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status: int, body: bytes = b"", kind: str = "application/json", headers: dict | None = None):
        self.send_response(status)
        for name, value in {"Content-Type": kind, "Content-Length": str(len(body)), **(headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class DevIdentityPage(Quiet):
    """/dev-idp/: the sign-in page, the mailbox, gateway observations and admin bootstrap."""

    store: Store

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/dev-idp/oauth2/authorize":
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            hidden = "".join(f'<input type="hidden" name="{key}" value="{html.escape(query.get(key, ""))}">' for key in ("state", "redirect_uri"))
            page = (
                "<!doctype html><meta charset=utf-8><title>Development sign-in</title>"
                "<body style='font:14px Arial;margin:60px auto;max-width:360px'><h1 style='font-size:18px'>Development sign-in</h1>"
                "<p>Stands in for Cognito on the local staging origin only.</p>"
                f"<form method=post action='/dev-idp/approve'>{hidden}"
                "<p><label>Email<br><input name=email type=email required style='width:100%'></label></p>"
                "<p><label>Name<br><input name=name style='width:100%'></label></p>"
                "<button type=submit>Sign in</button></form>"
            ).encode()
            return self.reply(200, page, "text/html; charset=utf-8")
        if url.path == "/dev-idp/mail":
            return self.reply(200, json.dumps(MAIL).encode())
        if url.path == "/dev-idp/seen":
            return self.reply(200, json.dumps(SEEN).encode())
        self.reply(404)

    def do_POST(self):
        url = urlsplit(self.path)
        form = {key: values[0] for key, values in parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()).items()}
        if url.path == "/dev-idp/approve":
            if not form.get("redirect_uri", "").startswith(ORIGIN + "/auth/callback"):
                return self.reply(400, b'{"error":"unexpected redirect"}')
            code = base64.urlsafe_b64encode(json.dumps({"email": form["email"], "name": form.get("name", "")}).encode()).decode()
            return self.reply(302, headers={"Location": form["redirect_uri"] + "?" + urlencode({"code": code, "state": form.get("state", "")})})
        if url.path == "/dev-idp/bootstrap-admin":
            # The same Store call as `python -m services.accounts.cli bootstrap-admin`.
            with self.store.pool.connection() as conn:
                row = conn.execute("SELECT id FROM app.users WHERE email=%s", (form.get("email", ""),)).fetchone()
            if not row:
                return self.reply(404, b'{"error":"sign in with this email first"}')
            self.store.bootstrap_admin(row["id"])
            return self.reply(200, json.dumps({"admin": form["email"]}).encode())
        self.reply(404)


class PublicRelay(Quiet):
    """A business service behind the gateway, answered by the public read-only API."""

    def do_GET(self):
        service = RELAYS[self.server.server_port]
        SEEN[service] = self.headers.get("X-Account-User-Id")
        if not SEEN[service] or self.headers.get("Cookie") or self.headers.get("Authorization"):
            return self.reply(500, b'{"detail":"gateway did not replace identity or strip credentials"}')
        try:
            with urllib.request.urlopen(PUBLIC_API + service + self.path, timeout=60) as response:
                return self.reply(response.status, response.read(), response.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as error:
            return self.reply(error.code, error.read(), error.headers.get("Content-Type", "application/json"))


class AgentStub(Quiet):
    """Answers Ask without a model, after checking what the gateway passed on."""

    def do_POST(self):
        question = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}").get("question", "")
        SEEN["agent"] = self.headers.get("X-Account-User-Id")
        if not SEEN["agent"] or self.headers.get("Cookie"):
            return self.reply(500, b'{"detail":"gateway did not replace identity or strip credentials"}')
        answer = {"answer_text": f"Staging agent: no model was called. You asked: {question}", "status": "answer"}
        events = [("progress", {"stage": "route"}), ("answer", answer)]
        body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()
        self.reply(200, body, "text/event-stream")


def nginx_config(root: Path, site: Path) -> str:
    source = Path(__file__).resolve().parents[2] / "deploy/nginx"
    for name in ("accounts-proxy.conf", "accounts-protected.conf"):
        (root / name).write_text((source / name).read_text(), encoding="utf-8")
    gateway = (source / "accounts-gateway.conf").read_text()
    gateway = gateway.replace("127.0.0.1:8080", "127.0.0.1:18080").replace("/etc/nginx/wildfire/", root.as_posix() + "/")
    for old, new in ((8000, 18000), (8001, 18001), (8002, 18002), (8004, 18004), (8005, 18005)):
        gateway = gateway.replace(f"127.0.0.1:{old}", f"127.0.0.1:{new}")
    temp = "".join(f"{kind}_temp_path {root}/{kind};\n" for kind in ("client_body", "proxy", "fastcgi", "scgi", "uwsgi"))
    front = f"""
server {{
    # Stands in for CloudFront: one HTTPS origin for the site, /auth/* and /api/*.
    listen 127.0.0.1:8443 ssl;
    server_name localhost;
    ssl_certificate {root}/localhost.crt;
    ssl_certificate_key {root}/localhost.key;
    error_log {root}/front.log warn;
    access_log off;
    root {site};
    location /assets/ {{ add_header Cache-Control "public, max-age=31536000, immutable"; }}
    # Like the CloudFront Function: page paths load index.html; API 404s stay 404s.
    location / {{ try_files $uri /index.html; add_header Cache-Control "no-cache"; }}
    location /auth/ {{ proxy_pass http://127.0.0.1:18080; proxy_set_header Host $host; }}
    location /api/ {{ proxy_pass http://127.0.0.1:18080; proxy_set_header Host $host; proxy_buffering off; proxy_read_timeout 300s; }}
    location /dev-idp/ {{ proxy_pass http://127.0.0.1:18006; }}
}}
"""
    types = "types { text/html html; text/css css; application/javascript js; application/json json; image/svg+xml svg; image/png png; font/woff2 woff2; }\n"
    return f"pid {root}/nginx.pid;\nevents {{ worker_connections 256; }}\nhttp {{\n{types}default_type application/octet-stream;\n{temp}{gateway}\n{front}\n}}\n"


def start_postgres(root: Path, binaries: Path, environment: dict) -> str:
    share = binaries.parents[3] / "share/postgresql" / binaries.parent.name
    subprocess.run([binaries / "initdb", "-D", root / "pg", "-L", share, "-U", "postgres", "-A", "trust", "--no-locale", "-E", "UTF8"],
                   check=True, env=environment, stdout=subprocess.DEVNULL)
    subprocess.run([binaries / "pg_ctl", "-D", root / "pg", "-l", root / "postgres.log", "-w", "-o", f"-h 127.0.0.1 -p {PG_PORT} -k {root}", "start"],
                   check=True, env=environment, stdout=subprocess.DEVNULL)
    server = f"postgresql://{{}}@127.0.0.1:{PG_PORT}/{{}}"
    with psycopg.connect(server.format("postgres", "postgres"), autocommit=True) as conn:
        conn.execute("CREATE DATABASE accounts_staging")
    # The superuser stands in for the migration role; the service runs as a restricted role.
    with psycopg.connect(server.format("postgres", "accounts_staging")) as conn:
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(migration.read_text(encoding="utf-8"))
        conn.execute("CREATE ROLE accounts_runtime LOGIN")
        grant_runtime(conn, "accounts_runtime")
    return server.format("accounts_runtime", "accounts_staging")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--postgres-bin", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    if root.exists():
        parser.error("Harness directory already exists")
    if not (args.site / "index.html").is_file():
        parser.error("Build the site first: npm run build:accounts in website/")
    root.mkdir(parents=True)
    site = root / "site"
    shutil.copytree(args.site, site)
    # Packages unpacked without installing keep their libraries beside the binaries.
    libraries = args.postgres_bin.resolve().parents[3] / "lib/x86_64-linux-gnu"
    environment = {**os.environ, "LD_LIBRARY_PATH": str(libraries)}
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1", "-keyout", root / "localhost.key", "-out", root / "localhost.crt"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    servers, nginx, accounts = [], None, None
    try:
        database = start_postgres(root, args.postgres_bin.resolve(), environment)
        settings = Settings(database_url=database, secret="staging-only-" + token(), public_origin=ORIGIN, issuer=ISSUER,
                            oidc_domain=ORIGIN + "/dev-idp", client_id="staging")
        store = Store(settings)
        DevIdentityPage.store = store
        app = create_app(settings, store=store, identity=DevIdentity(settings), mailer=DevMailer())
        accounts = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18005, log_level="warning"))
        Thread(target=accounts.run, daemon=True).start()
        handlers = {18006: DevIdentityPage, 18004: AgentStub, **{port: PublicRelay for port in RELAYS}}
        servers = [ThreadingHTTPServer(("127.0.0.1", port), handler) for port, handler in handlers.items()]
        for server in servers:
            Thread(target=server.serve_forever, daemon=True).start()
        (root / "nginx.conf").write_text(nginx_config(root, site), encoding="utf-8")
        command = [str(args.nginx), "-e", str(root / "nginx-startup.log"), "-p", str(root), "-c", str(root / "nginx.conf"), "-g", "daemon off;"]
        subprocess.run([*command, "-t"], check=True, env=environment, stderr=subprocess.DEVNULL)
        nginx = subprocess.Popen(command, env=environment)
        print(f"Staging ready: {ORIGIN} (self-signed certificate). Mail: {ORIGIN}/dev-idp/mail", flush=True)
        while not stopped.wait(0.5):
            if nginx.poll() is not None:
                raise RuntimeError("Nginx exited before harness shutdown")
    finally:
        if nginx:
            nginx.terminate()
            nginx.wait(timeout=10)
        if accounts:
            accounts.should_exit = True
        for server in servers:
            server.shutdown()
            server.server_close()
        if (root / "pg/postmaster.pid").exists():
            subprocess.run([args.postgres_bin / "pg_ctl", "-D", root / "pg", "-w", "stop"], env=environment, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
