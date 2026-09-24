"""SHA-256 of canonical JSON with sorted keys and UTF-8 encoding."""

import hashlib
import json
from typing import Any


def canonical_json_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
