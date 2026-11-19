"""Isolated Linux Nginx harness with protocol stubs, not an identity provider.

With --relay, deploy/nginx/accounts-relay.conf sits between the gateway and the
business stub, which then answers over HTTPS as the public API origin would,
with a certificate from a throwaway CA that only this harness trusts.
"""

from __future__ import annotations

import argparse
import json
import signal
import ssl
import subprocess
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

RELAY_HOST = "relay.test"
RELAY_TLS_PORT = 18443
# Business ports in accounts-gateway.conf; with --relay each maps to a relay server on 181xx.
BUSINESS_PORTS = (8000, 8001, 8002, 8004)


class ContractStub(BaseHTTPRequestHandler):
    post_calls = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.respond()

    def respond(self):
        if self.server.server_port == 18005 and self.path == "/internal/auth/active":
            cookie = self.headers.get("Cookie", "")
            if cookie != "session=active":
                self.send_response(403 if cookie == "session=pending" else 401)
            elif self.headers.get("X-Original-Method") == "POST" and (
                self.headers.get("Origin") != "https://accounts.test" or self.headers.get("X-CSRF-Token") != "csrf-test"
            ):
                self.send_response(403)
            else:
                self.send_response(204)
                self.send_header("X-Account-User-Id", "verified-user")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.server.server_port in (18004, RELAY_TLS_PORT) and self.command == "POST":
            ContractStub.post_calls += 1
        if self.path.endswith("/stream"):
            # Two events with a pause between them: a buffering proxy would hold the first.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: first\n\n")
            self.wfile.flush()
            time.sleep(3)
            self.wfile.write(b"data: second\n\n")
            return
        payload = json.dumps({
            "method": self.command, "path": self.path, "post_calls": ContractStub.post_calls,
            "user": self.headers.get("X-Account-User-Id"), "host": self.headers.get("Host"),
            "cookie": self.headers.get("Cookie"), "authorization": self.headers.get("Authorization"),
            "csrf": self.headers.get("X-CSRF-Token"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if "set-cookie" in self.path:
            self.send_header("Set-Cookie", "upstream=1; Path=/")
        self.end_headers()
        self.wfile.write(payload)


def relay_certificates(root: Path) -> ssl.SSLContext:
    """A throwaway CA and a certificate for RELAY_HOST; Nginx trusts only this CA."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(UTC)

    def name(text: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])

    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder().subject_name(name("Relay harness CA")).issuer_name(name("Relay harness CA"))
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf = (
        x509.CertificateBuilder().subject_name(name(RELAY_HOST)).issuer_name(ca.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(RELAY_HOST)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (root / "relay-ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root / "relay.crt").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (root / "relay.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(root / "relay.crt", root / "relay.key")
    return context


def relay_config(root: Path, source: Path) -> str:
    """deploy/nginx/accounts-relay.conf aimed at the TLS stub instead of the public API origin."""
    upstream = (source / "accounts-relay-upstream.conf").read_text()
    upstream = upstream.replace("/etc/ssl/certs/ca-certificates.crt", (root / "relay-ca.crt").as_posix())
    (root / "accounts-relay-upstream.conf").write_text(upstream, encoding="utf-8")
    relay = (source / "accounts-relay.conf").read_text()
    relay = relay.replace("d3t70p3if3twy3.cloudfront.net", RELAY_HOST).replace("/etc/nginx/wildfire/", root.as_posix() + "/")
    relay = relay.replace("/var/log/nginx/wildfire-relay.error.log", (root / "relay.log").as_posix())
    for port in BUSINESS_PORTS:
        relay = relay.replace(f"listen 127.0.0.1:{port};", f"listen 127.0.0.1:{port + 10100};")
    # A named upstream stands in for DNS, so the relay's own name, SNI and verification are what run.
    return f"upstream {RELAY_HOST} {{ server 127.0.0.1:{RELAY_TLS_PORT}; }}\n" + relay


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--relay", action="store_true", help="forward through deploy/nginx/accounts-relay.conf over verified TLS")
    args = parser.parse_args()
    root = args.directory.resolve()
    if root.exists():
        parser.error("Harness directory already exists")
    root.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "deploy/nginx"
    for name in ("accounts-proxy.conf", "accounts-protected.conf"):
        (root / name).write_text((source / name).read_text(), encoding="utf-8")
    gateway = (source / "accounts-gateway.conf").read_text()
    gateway = gateway.replace("127.0.0.1:8080", "127.0.0.1:18080").replace("/etc/nginx/wildfire/", root.as_posix() + "/")
    for old in BUSINESS_PORTS:
        gateway = gateway.replace(f"127.0.0.1:{old}/", f"127.0.0.1:{old + 10100 if args.relay else 18004}/")
    gateway = gateway.replace("127.0.0.1:8005", "127.0.0.1:18005")
    config = f"daemon off;\npid {root}/nginx.pid;\nevents {{ worker_connections 64; }}\nhttp {{\n"
    config += f"client_body_temp_path {root}/body;\nproxy_temp_path {root}/proxy;\n"
    for kind in ("fastcgi", "scgi", "uwsgi"):
        config += f"{kind}_temp_path {root}/{kind};\n"
    config += gateway + "\n"
    # The relay's trusted CA must exist before nginx -t reads the configuration.
    tls_context = relay_certificates(root) if args.relay else None
    if args.relay:
        config += relay_config(root, source) + "\n"
    config += "}\n"
    config_path = root / "nginx.conf"
    config_path.write_text(config, encoding="utf-8")
    command = [str(args.nginx), "-e", str(root / "startup.log"), "-p", str(root), "-c", str(config_path)]
    subprocess.run([*command, "-t"], check=True)
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    servers = [ThreadingHTTPServer(("127.0.0.1", port), ContractStub) for port in (18004, 18005)]
    if tls_context:
        tls = ThreadingHTTPServer(("127.0.0.1", RELAY_TLS_PORT), ContractStub)
        tls.socket = tls_context.wrap_socket(tls.socket, server_side=True)
        servers.append(tls)
    for server in servers:
        Thread(target=server.serve_forever, daemon=True).start()
    nginx = subprocess.Popen(command)
    try:
        print("Nginx contract harness: loopback 18080" + (" through the relay" if args.relay else ""), flush=True)
        while not stopped.wait(0.5):
            if nginx.poll() is not None:
                raise RuntimeError("Nginx exited before harness shutdown")
    finally:
        nginx.terminate()
        nginx.wait(timeout=10)
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
