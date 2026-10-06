"""HTTP probe for the demo entry point.

GET /health and GET /tenants/{tenant_id}/finance/budget are reads.
This function does not create purchase orders.
"""

from __future__ import annotations

import json
from typing import Any


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method != "GET":
        return _response(405, {"detail": "This entry point does not accept writes."})

    path = event.get("rawPath") or "/"
    if path == "/health":
        return _response(
            200,
            {
                "status": "ok",
                "execution_profile": "aws-demo",
                "local_deterministic_simulation": False,
            },
        )

    if path.startswith("/tenants/") and path.endswith("/finance/budget"):
        tenant_id = path.split("/")[2]
        return _response(
            200,
            {
                "tenant_id": tenant_id,
                "available_cents": 0,
                "committed_cents": 0,
                "observed_at": "1970-01-01T00:00:00Z",
            },
        )

    return _response(404, {"detail": "Not found"})


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
