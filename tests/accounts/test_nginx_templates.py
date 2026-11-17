"""Invariants across the Nginx templates, read as text (no Nginx needed).

The gateway, the sign-in-only relay, the single-host HTTPS site and the website's
accounts build name the same ports and API prefixes in different files. These
tests fail when one of them changes without the others.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "deploy/nginx"


def read(name: str) -> str:
    return (NGINX / name).read_text(encoding="utf-8")


def blocks(text: str, keyword: str) -> list[tuple[str, str]]:
    """(argument, body) of every `keyword argument { body }` without nested braces."""
    return re.findall(keyword + r"\s+([^{;]*?)\s*\{([^{}]*)\}", text)


def protected_routes() -> dict[int, str]:
    """Business port -> public API prefix, from the gateway's protected locations."""
    routes = {}
    for prefix, body in blocks(read("accounts-gateway.conf"), "location"):
        if "accounts-protected.conf" in body:
            port = re.search(r"proxy_pass http://127\.0\.0\.1:(\d+)/;", body)
            routes[int(port.group(1))] = prefix.rstrip("/")
    return routes


def relay_servers() -> list[str]:
    text = read("accounts-relay.conf")
    return [server for server in re.split(r"\nserver\s*\{", text)[1:]]


def test_gateway_protects_the_four_business_services():
    assert protected_routes() == {8000: "/api/data-query", 8001: "/api/risk-forecasting", 8002: "/api/visualization", 8004: "/api/agent"}


def test_relay_answers_every_protected_port_with_its_public_path():
    relayed = {}
    for server in relay_servers():
        port = int(re.search(r"listen 127\.0\.0\.1:(\d+);", server).group(1))
        target = re.search(r"proxy_pass https://\$wildfire_api_host(/api/[a-z-]+)\$request_uri;", server).group(1)
        assert "include /etc/nginx/wildfire/accounts-relay-upstream.conf;" in server
        assert "access_log off;" in server
        relayed[port] = target
    assert relayed == protected_routes()


def test_relay_streams_the_agent_like_the_gateway():
    agent = next(server for server in relay_servers() if "listen 127.0.0.1:8004;" in server)
    assert "proxy_buffering off;" in agent and "proxy_read_timeout 300s;" in agent


def test_relay_upstream_verifies_tls_and_keeps_account_data_home():
    upstream = read("accounts-relay-upstream.conf")
    for line in (
        "proxy_ssl_verify on;", "proxy_ssl_server_name on;", "proxy_ssl_name $wildfire_api_host;",
        "proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;", "proxy_set_header Host $wildfire_api_host;",
        'proxy_set_header X-CSRF-Token "";', "proxy_hide_header Set-Cookie;",
    ):
        assert line in upstream


def test_relay_targets_the_api_origin_the_public_website_uses():
    relay_host = re.search(r"default ([a-z0-9.-]+);", read("accounts-relay.conf")).group(1)
    production = (ROOT / "website/.env.production").read_text(encoding="utf-8")
    hosts = {urlsplit(url).hostname for url in re.findall(r"^VITE_\w+_URL=(\S+)$", production, re.MULTILINE)}
    assert hosts == {relay_host}


def test_accounts_build_calls_only_protected_prefixes():
    build = (ROOT / "website/.env.accounts").read_text(encoding="utf-8")
    paths = set(re.findall(r"^VITE_\w+_URL=(\S+)$", build, re.MULTILINE))
    assert paths == set(protected_routes().values())


def test_site_sends_auth_and_api_to_the_gateway():
    site = read("accounts-site.conf")
    gateway = re.search(r"listen (127\.0\.0\.1:\d+);", read("accounts-gateway.conf")).group(1)
    locations = dict(blocks(site, "location"))
    for prefix in ("/auth/", "/api/"):
        assert f"proxy_pass http://{gateway};" in locations[prefix]
        assert "proxy_set_header Host $host;" in locations[prefix]
    assert "proxy_buffering off;" in locations["/api/"] and "proxy_read_timeout 300s;" in locations["/api/"]
    assert "try_files $uri /index.html;" in locations["/"]


def test_every_site_location_sends_the_security_headers():
    locations = blocks(read("accounts-site.conf"), "location")
    assert locations
    for prefix, body in locations:
        assert "include /etc/nginx/wildfire/security-headers.conf;" in body, prefix


def test_site_and_port_80_keep_no_request_logs():
    assert "access_log off;" in read("accounts-site.conf")
    assert "access_log off;" in read("accounts-site-http.conf")
    assert "return 301 https://$host$request_uri;" in read("accounts-site-http.conf")
