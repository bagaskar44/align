from __future__ import annotations

import socket
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: readiness.py ROOT_CA_CERT")
    ca_path = Path(sys.argv[1]).resolve()
    if not ca_path.is_file():
        raise SystemExit("B9 CA certificate is missing")
    checks = (
        ("align-b9.localhost", "https://align-b9.localhost:8443/health/ready"),
        ("moodle-b9.localhost", "https://moodle-b9.localhost:8443/login/index.php"),
    )
    with ExitStack() as stack:
        if sys.platform == "win32":
            # Windows resolves localhost itself but not arbitrary .localhost subdomains.
            # Keep the hostname for SNI/certificate validation while routing this check to loopback.
            original_getaddrinfo = socket.getaddrinfo

            def resolve_b9_localhost(
                host: Any,
                port: Any,
                *args: Any,
                **kwargs: Any,
            ) -> Any:
                if isinstance(host, str) and host.lower().rstrip(".") in {
                    "align-b9.localhost",
                    "moodle-b9.localhost",
                }:
                    host = "127.0.0.1"
                return original_getaddrinfo(host, port, *args, **kwargs)

            stack.enter_context(
                patch("socket.getaddrinfo", side_effect=resolve_b9_localhost)
            )

        with httpx.Client(verify=str(ca_path), timeout=15, follow_redirects=False) as client:
            for host, url in checks:
                try:
                    response = client.get(url)
                except httpx.HTTPError as exc:
                    raise SystemExit(
                        f"B9 readiness failed for {host}: TLS or connection check failed"
                    ) from exc
                if not 200 <= response.status_code < 400:
                    raise SystemExit(f"B9 readiness failed for {host}: HTTP {response.status_code}")
                print(f"ready {host} status={response.status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
