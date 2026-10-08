"""Task entry for the approval wait.

The control plane applies create_draft, expire_approval, and notify through
the approval and draft services. This function does not call a model.
"""

from __future__ import annotations

from typing import Any

_STATES = {
    "wait": "waiting",
    "create_draft": "draft_created",
    "notify": "notified",
    "expire_approval": "expired",
}


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    payload = event.get("Payload")
    body = payload if isinstance(payload, dict) else event
    action = body.get("action")
    if not isinstance(action, str) or action not in _STATES:
        return {"ok": False, "reason": "unknown_action"}
    return {
        "ok": True,
        "action": action,
        "state": _STATES[action],
        "proposal_id": body.get("proposal_id"),
    }
