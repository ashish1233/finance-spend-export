"""A fake identity provider, warehouse and audit sink, in-process.

The platform stubs listen on 8081/8082/8083, but a suite that needs them running
is a suite that gets skipped. This starts a throwaway HTTP server on an
ephemeral port so `pytest` works with nothing else running.

The fake IdP is the interesting part. It serves exactly one endpoint —
`POST /token/service` — and it can only mint service tokens. A job that wanted
to run as a user would have to obtain that token somewhere other than the
harness, which is the failure the test is looking for.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from insights_platform import PrincipalKind, issue_token

TENANT = "finance"

# What the app asked the platform for, and what it recorded.
TOKEN_REQUESTS: list[dict] = []
AUDIT_EVENTS: list[dict] = []

# The column shape of `stubs/warehouse`'s `spend` dataset, plus one deliberately
# malformed row: a single bad amount should be counted, not fatal.
SPEND_ROWS = [
    {
        "cost_center": "CC-100",
        "category": "Cloud Infrastructure",
        "quarter": "2026-Q1",
        "amount": 250000,
        "currency": "USD",
    },
    {
        "cost_center": "CC-100",
        "category": "Cloud Infrastructure",
        "quarter": "2026-Q2",
        "amount": 275000,
        "currency": "USD",
    },
    {
        "cost_center": "CC-200",
        "category": "Software Licences",
        "quarter": "2026-Q1",
        "amount": 90000,
        "currency": "USD",
    },
    {
        "cost_center": "CC-300",
        "category": "Travel",
        "quarter": "2026-Q1",
        "amount": "not-a-number",
        "currency": "USD",
    },
]


class _FakePlatform(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/token/service":
            TOKEN_REQUESTS.append(body)
            token = issue_token(
                kind=PrincipalKind.SERVICE,
                subject=body["service"],
                tenant_id=body["tenant_id"],
                scopes={"export:run", "insights:read"},
            )
            return self._respond(200, {"token": token})

        if self.path == "/events":
            AUDIT_EVENTS.append(body)
            return self._respond(200, {"recorded": True})

        if self.path == "/query":
            # Injected by the SDK, never caller-supplied.
            assert body["tenant_id"] == TENANT
            rows = SPEND_ROWS if body.get("dataset") == "spend" else []
            return self._respond(200, {"rows": rows})

        self._respond(404, {"detail": "not a stubbed path"})

    def log_message(self, *args) -> None:  # keep pytest output readable
        pass


_server = HTTPServer(("127.0.0.1", 0), _FakePlatform)
threading.Thread(target=_server.serve_forever, daemon=True).start()

_base = f"http://127.0.0.1:{_server.server_port}"
os.environ["INSIGHTS_TENANT_ID"] = TENANT
os.environ["INSIGHTS_TIER"] = "standard"
os.environ["INSIGHTS_IDP_URL"] = _base
os.environ["INSIGHTS_WAREHOUSE_URL"] = _base
os.environ["INSIGHTS_AUDIT_URL"] = _base
# Production shape: one run, then return. The demo loop would never terminate.
os.environ["EXPORT_INTERVAL_SECONDS"] = "0"
