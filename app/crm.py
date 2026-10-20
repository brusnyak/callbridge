"""CRM adapter: SQLite lead write + signed outbound webhook (best-effort)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.request

from .db import Store, hash_caller


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def record_lead(store: Store, call_id: str, name: str | None,
                phone: str | None, note: str | None) -> dict:
    phone_hash = hash_caller(phone) if phone else None
    store.save_lead(call_id, name, phone_hash, note)
    payload = {"call_id": call_id, "name": name,
               "phone_hash": phone_hash, "note": note}
    delivered = post_webhook(payload)
    return {"stored": True, "webhook_delivered": delivered}


def post_webhook(payload: dict) -> bool:
    url = os.environ.get("CALLBRIDGE_CRM_URL", "")
    if not url:
        return False  # no URL configured: stored locally, not delivered
    secret = os.environ.get("CALLBRIDGE_SECRET", "")
    # FIX: no "dev-secret" fallback; and only http(s) URLs (no file:// etc).
    if not secret or not url.startswith(("http://", "https://")):
        return False
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Callbridge-Signature": sign_payload(secret, body)},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return 200 <= r.status < 300
    except Exception:
        return False
