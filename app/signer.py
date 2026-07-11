from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import uuid4


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def sign_payload(secret: str, payload: dict) -> str:
    body = canonical_json(payload).encode("utf-8")
    key = secret.encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def build_replay_metadata() -> tuple[str, str]:
    issued_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    nonce = uuid4().hex
    return issued_at, nonce
