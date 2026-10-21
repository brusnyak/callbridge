"""Postgres store tests (app/db_postgres.py).

Skipped unless DATABASE_URL points at a live Postgres, so the default
`pytest -q` run needs no infrastructure:

    docker compose up -d db
    DATABASE_URL=postgresql+psycopg://callbridge:callbridge-local-dev@127.0.0.1:5432/callbridge \
      pytest tests/test_postgres.py
"""
import os
import uuid

import pytest

from app.db_postgres import PostgresStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set: no Postgres to test against",
)


@pytest.fixture
def store():
    return PostgresStore(os.environ["DATABASE_URL"])


def test_event_is_stored_once_and_replayed(store):
    eid = "ev_" + uuid.uuid4().hex[:8]
    assert store.seen_event(eid) is None
    store.mark_event(eid, {"ok": True, "n": 1})
    assert store.seen_event(eid) == {"ok": True, "n": 1}
    store.mark_event(eid, {"ok": True, "n": 2})  # must not raise
    assert store.seen_event(eid) == {"ok": True, "n": 1}


def test_duplicate_event_id_blocks_second_booking(store):
    eid = "ev_" + uuid.uuid4().hex[:8]
    cid = "call_" + uuid.uuid4().hex[:8]
    assert store.save_booking("bk_a_" + eid, eid, cid,
                              "2026-10-20T10:00:00+02:00",
                              "2026-10-20T10:30:00+02:00") is True
    assert store.save_booking("bk_b_" + eid, eid, cid,
                              "2026-10-20T11:00:00+02:00",
                              "2026-10-20T11:30:00+02:00") is False
    assert store.booking_for_call(cid) is not None


def test_save_call_upserts_and_lists(store):
    cid = "call_" + uuid.uuid4().hex[:8]
    store.save_call(cid, "hash123", "2026-10-17T10:00:00+02:00",
                    "completed", None, 100.0, 200.0, 12)
    store.save_call(cid, "hash123", "2026-10-17T10:00:00+02:00",
                    "booked", "bk_x", 100.0, 200.0, 12)
    rows = [r for r in store.list_calls() if r["id"] == cid]
    assert len(rows) == 1 and rows[0]["outcome"] == "booked"
    assert rows[0]["p50_ms"] == 100.0


def test_lead_and_counts(store):
    cid = "call_" + uuid.uuid4().hex[:8]
    store.save_lead(cid, "Jana", "phonehash", "note")
    assert store.count("leads") >= 1
    assert store.health() == "ok"
