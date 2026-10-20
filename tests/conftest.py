import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CALLBRIDGE_SECRET"] = "test-secret"
os.environ["CALLBRIDGE_DB"] = ":memory:"
os.environ.pop("CALLBRIDGE_CRM_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as M  # noqa: E402

SECRET = "test-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client():
    M.store = M.Store(":memory:")
    M.calendar = M.FakeCalendar()
    return TestClient(M.app)


@pytest.fixture
def post(client):
    def _post(payload, sig=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        h = {"Content-Type": "application/json"}
        h["X-Callbridge-Signature"] = sign(body) if sig is None else sig
        if sig == "":
            del h["X-Callbridge-Signature"]
        return client.post("/vapi/webhook", content=body, headers=h)
    return _post


def book_ev(eid, start, call="c1"):
    return {"event": {"id": eid, "type": "tool-calls"},
            "tool": {"name": "book_appointment", "call_id": call, "args": {"start": start}}}
