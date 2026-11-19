"""Print the CloudFront security headers policy as Nginx add_header lines.

The website served from a single Nginx host (deploy/SIGN_IN_ONLY.md) and the
staging harness send exactly the policy in deploy/cloudfront/security-headers.json:

    python3 deploy/nginx/security_headers.py | sudo tee /etc/nginx/wildfire/security-headers.conf
"""

from __future__ import annotations

import json
from pathlib import Path

POLICY = Path(__file__).resolve().parents[1] / "cloudfront/security-headers.json"


def security_headers(policy_path: Path = POLICY) -> str:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    security = policy["SecurityHeadersConfig"]
    hsts = security["StrictTransportSecurity"]
    headers = {
        "Content-Security-Policy": security["ContentSecurityPolicy"]["ContentSecurityPolicy"],
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": security["FrameOptions"]["FrameOption"],
        "Referrer-Policy": security["ReferrerPolicy"]["ReferrerPolicy"],
        "Strict-Transport-Security": f"max-age={hsts['AccessControlMaxAgeSec']}"
        + ("; includeSubDomains" if hsts["IncludeSubdomains"] else "") + ("; preload" if hsts["Preload"] else ""),
        **{item["Header"]: item["Value"] for item in policy["CustomHeadersConfig"]["Items"]},
    }
    return "".join(f'add_header {name} "{value}" always;\n' for name, value in headers.items())


if __name__ == "__main__":
    print(security_headers(), end="")
