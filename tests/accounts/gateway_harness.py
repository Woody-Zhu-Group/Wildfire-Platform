"""Isolated Linux Nginx harness with protocol stubs, not an identity provider."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread


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
        if self.server.server_port == 18004 and self.command == "POST":
            ContractStub.post_calls += 1
        payload = json.dumps({
            "method": self.command, "path": self.path, "post_calls": ContractStub.post_calls,
            "user": self.headers.get("X-Account-User-Id"),
            "cookie": self.headers.get("Cookie"), "authorization": self.headers.get("Authorization"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
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
    for old in (8000, 8001, 8002, 8004):
        gateway = gateway.replace(f"127.0.0.1:{old}/", "127.0.0.1:18004/")
    gateway = gateway.replace("127.0.0.1:8005", "127.0.0.1:18005")
    config = f"daemon off;\npid {root}/nginx.pid;\nevents {{ worker_connections 64; }}\nhttp {{\n"
    config += f"client_body_temp_path {root}/body;\nproxy_temp_path {root}/proxy;\n"
    for kind in ("fastcgi", "scgi", "uwsgi"):
        config += f"{kind}_temp_path {root}/{kind};\n"
    config += gateway + "\n}\n"
    config_path = root / "nginx.conf"
    config_path.write_text(config, encoding="utf-8")
    command = [str(args.nginx), "-e", str(root / "startup.log"), "-p", str(root), "-c", str(config_path)]
    subprocess.run([*command, "-t"], check=True)
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    servers = [ThreadingHTTPServer(("127.0.0.1", port), ContractStub) for port in (18004, 18005)]
    for server in servers:
        Thread(target=server.serve_forever, daemon=True).start()
    nginx = subprocess.Popen(command)
    try:
        print("Nginx contract harness: loopback 18080", flush=True)
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
