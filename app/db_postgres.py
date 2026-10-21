"""Postgres store: SQLAlchemy 2.x Core (no ORM, no migration framework).

Selected with STORE=postgres. Same duck-typed interface as app.db.Store
(seen_event, mark_event, save_call, save_booking, save_lead,
booking_for_call, count, list_calls, health), so app/main.py and
app/crm.py work unchanged against either store.

Driver: psycopg 3 (not asyncpg) - psycopg 3 is the driver PostgreSQL
itself now ships against, SQLAlchemy 2.x supports it natively, and it
keeps one synchronous code path instead of a second async engine.

Tables are created with metadata.create_all() on startup: no Alembic,
no migration framework, per the repo constraints.
"""
from __future__ import annotations

import json
import time

from sqlalchemy import (Column, Float, Integer, MetaData, Table, Text, create_engine,
                        func, select, text)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

TZ = "Europe/Bratislava"

metadata = MetaData()

calls = Table(
    "calls", metadata,
    Column("id", Text, primary_key=True),
    Column("caller_hash", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("booking_id", Text),
    Column("p50_ms", Float),
    Column("p95_ms", Float),
    Column("transcript_len", Integer, server_default=text("0")),
    Column("created_at", Text, server_default=text("now()::text")),
)

bookings = Table(
    "bookings", metadata,
    Column("id", Text, primary_key=True),
    Column("event_id", Text, nullable=False, unique=True),
    Column("call_id", Text, nullable=False),
    Column("start", Text, nullable=False),
    Column("end", Text, nullable=False),
    Column("tz", Text, nullable=False, server_default=TZ),
    Column("status", Text, nullable=False, server_default="confirmed"),
)

leads = Table(
    "leads", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("call_id", Text, nullable=False),
    Column("name", Text),
    Column("phone_hash", Text),
    Column("note", Text),
    Column("created_at", Text, server_default=text("now()::text")),
)

processed_events = Table(
    "processed_events", metadata,
    Column("event_id", Text, primary_key=True),
    Column("result_json", Text, nullable=False),
    Column("created_at", Text, server_default=text("now()::text")),
)

TABLES = {"calls": calls, "bookings": bookings,
          "leads": leads, "processed_events": processed_events}


class PostgresStore:
    kind = "postgres"

    def __init__(self, url: str, attempts: int = 5, delay: float = 2.0):
        if not url:
            raise RuntimeError("STORE=postgres needs DATABASE_URL")
        self.url = url
        self.engine = create_engine(url, pool_pre_ping=True)
        self._create_tables(attempts, delay)

    def _create_tables(self, attempts: int, delay: float) -> None:
        """Retry while Postgres is still booting (docker compose race)."""
        last = None
        for _ in range(attempts):
            try:
                metadata.create_all(self.engine)
                return
            except SQLAlchemyError as exc:
                last = exc
                time.sleep(delay)
        raise RuntimeError(f"postgres not reachable: {last}")

    # --- events -----------------------------------------------------------
    def seen_event(self, event_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(processed_events.c.result_json)
                .where(processed_events.c.event_id == event_id)
            ).first()
        return json.loads(row[0]) if row else None

    def mark_event(self, event_id: str, result: dict) -> None:
        stmt = pg_insert(processed_events).values(
            event_id=event_id, result_json=json.dumps(result)
        ).on_conflict_do_nothing()
        with self.engine.begin() as conn:
            conn.execute(stmt)

    # --- calls / bookings / leads ----------------------------------------
    def save_call(self, call_id: str, caller_hash: str, started_at: str,
                  outcome: str, booking_id: str | None,
                  p50: float | None, p95: float | None, tlen: int) -> None:
        values = dict(id=call_id, caller_hash=caller_hash, started_at=started_at,
                      outcome=outcome, booking_id=booking_id, p50_ms=p50,
                      p95_ms=p95, transcript_len=tlen)
        stmt = pg_insert(calls).values(**values).on_conflict_do_update(
            index_elements=[calls.c.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def save_booking(self, bid: str, event_id: str, call_id: str,
                     start: str, end: str, tz: str = TZ) -> bool:
        # RETURNING tells us whether the row landed. Do NOT use rowcount:
        # psycopg reports -1 for INSERT ... ON CONFLICT DO NOTHING.
        stmt = (pg_insert(bookings)
                .values(id=bid, event_id=event_id, call_id=call_id, start=start, end=end, tz=tz)
                .on_conflict_do_nothing()
                .returning(bookings.c.id))
        with self.engine.begin() as conn:
            row = conn.execute(stmt).first()
        return row is not None  # False = duplicate event id, booking blocked

    def save_lead(self, call_id: str, name: str | None,
                  phone_hash: str | None, note: str | None) -> None:
        stmt = pg_insert(leads).values(call_id=call_id, name=name,
                                       phone_hash=phone_hash, note=note)
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def booking_for_call(self, call_id: str) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(bookings.c.id)
                .where(bookings.c.call_id == call_id,
                       bookings.c.status == "confirmed")
                .limit(1)
            ).first()
        return row[0] if row else None

    # --- dashboard / ops ---------------------------------------------------
    def count(self, table: str) -> int:
        assert table in TABLES
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(TABLES[table])).scalar_one()

    def list_calls(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(calls).order_by(calls.c.created_at.desc()).limit(200)
            ).all()
        return [dict(r._mapping) for r in rows]

    def health(self) -> str:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return "ok"
        except SQLAlchemyError:
            return "unavailable"
