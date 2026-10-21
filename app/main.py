"""callbridge tool server: Vapi-style webhook + dashboard."""
from __future__ import annotations

import hashlib
import hmac
import html
import os
from datetime import timedelta
from datetime import datetime
import re
import uuid

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .calendar_fake import FakeCalendar
from .crm import record_lead
from .db import Store, hash_caller, latencies

SECRET_ENV = "CALLBRIDGE_SECRET"
TZ = "Europe/Bratislava"

INJECTION_PATTERNS = re.compile(
    r"(ignore (all )?previous instructions|system prompt|reveal your|jailbreak|"
    r"disregard (all )?(prior|above)|do anything now)",
    re.IGNORECASE,
)

app = FastAPI(title="callbridge")
calendar = FakeCalendar()


def build_store():
    """STORE=memory (default, SQLite) or STORE=postgres (SQLAlchemy 2.x).

    The Postgres store is imported lazily so the memory path needs no
    database driver at all.
    """
    kind = os.environ.get("STORE", "memory").strip().lower()
    if kind == "postgres":
        from .db_postgres import PostgresStore
        return PostgresStore(os.environ.get("DATABASE_URL", ""))
    if kind != "memory":
        raise RuntimeError(f"unknown STORE={kind!r}; use 'memory' or 'postgres'")
    return Store(os.environ.get("CALLBRIDGE_DB", "callbridge.db"))


store = build_store()


def verify(secret: str, body: bytes, sig: str | None) -> bool:
    if not sig or not sig.startswith("sha256="):
        return False
    want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


def get_secret() -> str:
    # FIX: no "dev-secret" fallback (anyone could forge events). Unset = fail closed.
    return os.environ.get(SECRET_ENV, "")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/health")
def health():
    """Liveness + which store is in use + whether the database answers."""
    try:
        db_state = store.health()
    except Exception:
        db_state = "unavailable"
    return {"status": "ok", "store": getattr(store, "kind", "memory"), "db": db_state}


@app.post("/vapi/webhook")
async def webhook(request: Request,
                  x_callbridge_signature: str | None = Header(default=None)):
    raw = await request.body()
    secret = get_secret()
    if not secret:
        return JSONResponse({"error": "server secret not configured"}, status_code=503)
    if not verify(secret, raw, x_callbridge_signature):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        event = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    if not isinstance(event, dict) or not isinstance(event.get("event"), dict):
        return JSONResponse({"error": "invalid event shape"}, status_code=400)

    eid = (event.get("event") or {}).get("id", "")
    if not eid:
        return JSONResponse({"error": "missing event.id"}, status_code=400)
    cached = store.seen_event(eid)
    if cached is not None:  # idempotent replay
        return {"ok": True, "duplicate": True, "result": cached}

    etype = (event.get("event") or {}).get("type", "")
    if etype == "tool-calls":
        result = handle_tool_call(event)
    elif etype == "end-of-call-report":
        result = handle_report(event)
    else:
        result = {"error": f"unknown event type: {etype}"}
    store.mark_event(eid, result)
    return {"ok": True, "duplicate": False, "result": result}


def handle_tool_call(event: dict) -> dict:
    tool = event.get("tool") or {}
    name = tool.get("name", "")
    args = tool.get("args") or event.get("args") or {}  # SPEC puts args top-level; accept both
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object"}
    call_id = tool.get("call_id", event.get("event", {}).get("id", ""))
    if name == "check_availability":
        date = args.get("date", "")
        try:
            slots = calendar.check_availability(date)
        except ValueError:
            return {"ok": False, "error": "bad date, expected YYYY-MM-DD"}
        return {"ok": True, "tool": name, "slots": slots}
    if name == "book_appointment":
        start = args.get("start", "")
        ok, info = calendar.book(start)
        if not ok:
            return {"ok": False, "tool": name, "error": info,
                    "hint": "offer check_availability alternatives"}
        bid = "bk_" + uuid.uuid4().hex[:8]
        # FIX: end was stored equal to start; store the real slot end.
        end = (datetime.fromisoformat(info) + timedelta(minutes=30)).isoformat()
        saved = store.save_booking(bid, event["event"]["id"], call_id,
                                   info, end, TZ)
        if not saved:  # duplicate event raced past the cache
            return {"ok": False, "tool": name, "error": "duplicate booking blocked"}
        return {"ok": True, "tool": name, "booking_id": bid, "start": info}
    return {"ok": False, "error": f"unknown tool: {name}"}


def guardrail(transcript: str) -> str | None:
    """Returns a block reason, or None if clean."""
    if INJECTION_PATTERNS.search(transcript or ""):
        return "prompt-injection attempt blocked"
    return None


def handle_report(event: dict) -> dict:
    call = event.get("call") or {}
    call_id = call.get("id", event["event"]["id"])
    caller = call.get("caller", "unknown")
    transcript = event.get("transcript", "")
    blocked = guardrail(transcript)
    try:
        p50, p95 = latencies([float(x) for x in (event.get("latencies_ms") or [])])
    except (TypeError, ValueError):
        p50, p95 = None, None  # FIX: junk latency values used to 500
    outcome = "completed"
    booking_id = None
    lowered = transcript.lower()
    wants_booking = any(k in lowered for k in (
        "book", "termin", "dohodnut", "dohodnúť", "appointment",
        "call me back", "zavolaj", "zavolajte",
    ))
    if blocked:
        outcome = "blocked:" + blocked
    elif store.booking_for_call(call_id):
        # FIX: booked status came from the payload's own "booking_id" (untrusted);
        # now only a booking actually stored for this call counts.
        booking_id = store.booking_for_call(call_id)
        outcome = "booked"
    elif wants_booking and "cancel" not in lowered:
        outcome = "interested-no-booking"
    store.save_call(call_id, hash_caller(caller),
                    call.get("started_at", ""), outcome, booking_id,
                    p50, p95, len(transcript))
    lead = event.get("lead") or {}
    crm = record_lead(store, call_id, lead.get("name"),
                      lead.get("phone"), transcript[:500] or None)
    return {"ok": True, "outcome": outcome, "crm": crm,
            "p50_ms": p50, "p95_ms": p95}


@app.get("/api/calls")
def api_calls():
    return {"calls": store.list_calls()}


@app.get("/calls", response_class=HTMLResponse)
def calls_page():
    rows = store.list_calls()

    def e(v):
        # FIX: payload strings were rendered unescaped (XSS)
        return html.escape(str(v))
    trs = "\n".join(
        f"<tr><td>{e(r['started_at'])}</td><td>{e(r['caller_hash'])}</td>"
        f"<td>{e(r['outcome'])}</td><td>{e(r.get('booking_id') or '—')}</td>"
        f"<td>{e(r['p50_ms']) if r.get('p50_ms') is not None else '—'}</td>"
        f"<td>{e(r['p95_ms']) if r.get('p95_ms') is not None else '—'}</td></tr>"
        for r in rows
    ) or '<tr><td colspan="6">no calls yet</td></tr>'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>callbridge — calls</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;
padding:0 1rem}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:.4rem .6rem;text-align:left}}
th{{background:#f4f4f4}}</style></head><body>
<h1>callbridge — calls</h1>
<table><tr><th>time</th><th>caller</th><th>outcome</th><th>booking</th>
<th>p50 ms</th><th>p95 ms</th></tr>{trs}</table></body></html>"""
