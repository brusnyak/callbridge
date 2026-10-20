"""SQLite storage. No PII stored raw — caller hashed with SHA256."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import statistics
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    caller_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    booking_id TEXT,
    p50_ms REAL,
    p95_ms REAL,
    transcript_len INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    event_id TEXT UNIQUE NOT NULL,
    call_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    tz TEXT NOT NULL DEFAULT 'Europe/Bratislava',
    status TEXT NOT NULL DEFAULT 'confirmed'
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    name TEXT,
    phone_hash TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def hash_caller(raw: str) -> str:
    # FIX: keyed HMAC instead of bare SHA256. Phone numbers have a tiny
    # search space, so an unkeyed 12-hex hash is trivially reversible.
    key = (os.environ.get("CALLBRIDGE_HASH_KEY")
           or os.environ.get("CALLBRIDGE_SECRET") or "").encode()
    return hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()[:12]


def percentile(data: list[float], pct: float) -> float | None:
    if not data:
        return None
    if len(data) == 1:
        return float(data[0])
    # FIX: "inclusive" keeps results inside the observed range (default
    # "exclusive" extrapolates past max on small samples, e.g. p95 of 3 points).
    return float(statistics.quantiles(sorted(data), n=100,
                                      method="inclusive")[min(int(pct) - 1, 98)])


def latencies(lat: list[float]) -> tuple[float | None, float | None]:
    return percentile(lat, 50), percentile(lat, 95)


class Store:
    def __init__(self, path: str | Path = "callbridge.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def seen_event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT result_json FROM processed_events WHERE event_id=?", (event_id,)
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def mark_event(self, event_id: str, result: dict) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_events(event_id, result_json) VALUES(?,?)",
            (event_id, json.dumps(result)),
        )
        self.conn.commit()

    def save_call(self, call_id: str, caller_hash: str, started_at: str,
                  outcome: str, booking_id: str | None,
                  p50: float | None, p95: float | None, tlen: int) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO calls
               (id, caller_hash, started_at, outcome, booking_id, p50_ms, p95_ms, transcript_len)
               VALUES(?,?,?,?,?,?,?,?)""",
            (call_id, caller_hash, started_at, outcome, booking_id, p50, p95, tlen),
        )
        self.conn.commit()

    def save_booking(self, bid: str, event_id: str, call_id: str,
                     start: str, end: str, tz: str = "Europe/Bratislava") -> bool:
        try:
            self.conn.execute(
                "INSERT INTO bookings(id, event_id, call_id, start, end, tz) VALUES(?,?,?,?,?,?)",
                (bid, event_id, call_id, start, end, tz),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def save_lead(self, call_id: str, name: str | None,
                  phone_hash: str | None, note: str | None) -> None:
        self.conn.execute(
            "INSERT INTO leads(call_id, name, phone_hash, note) VALUES(?,?,?,?)",
            (call_id, name, phone_hash, note),
        )
        self.conn.commit()

    def booking_for_call(self, call_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM bookings WHERE call_id=? AND status='confirmed' LIMIT 1",
            (call_id,)).fetchone()
        return row["id"] if row else None

    def count(self, table: str) -> int:
        assert table in ("calls", "bookings", "leads", "processed_events")
        return self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    def list_calls(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM calls ORDER BY created_at DESC LIMIT 200").fetchall()
        return [dict(r) for r in rows]
