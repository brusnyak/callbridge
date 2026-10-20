"""Calendar adapters: fake in-memory (used) + Google (documented, not executed)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Bratislava")

# Business hours: Mon–Fri 09:00–17:00 Bratislava, 30-min slots.


class FakeCalendar:
    """In-memory slots with conflict detection. Deterministic for evals."""

    def __init__(self):
        self.busy: list[tuple[datetime, datetime]] = []

    def _day_slots(self, day: datetime) -> list[tuple[datetime, datetime]]:
        slots = []
        cur = day.replace(hour=9, minute=0, second=0, microsecond=0)
        end = day.replace(hour=17, minute=0, second=0, microsecond=0)
        while cur + timedelta(minutes=30) <= end:
            slots.append((cur, cur + timedelta(minutes=30)))
            cur += timedelta(minutes=30)
        return slots

    def check_availability(self, date: str) -> list[dict]:
        """date: YYYY-MM-DD. Returns free slots as ISO strings."""
        day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=TZ)
        if day.weekday() >= 5:
            return []
        free = []
        for s, e in self._day_slots(day):
            if not any(s < b_e and e > b_s for b_s, b_e in self.busy):
                free.append({"start": s.isoformat(), "end": e.isoformat()})
        return free

    def book(self, start_iso: str, minutes: int = 30) -> tuple[bool, str]:
        """Returns (ok, booking_start_iso | conflict_reason)."""
        try:
            start = datetime.fromisoformat(start_iso)
        except ValueError:
            return False, "invalid datetime, expected ISO 8601"
        if start.tzinfo is None:
            start = start.replace(tzinfo=TZ)
        else:
            start = start.astimezone(TZ)
        if start.weekday() >= 5:
            return False, "weekend: no slots"
        if not (9 <= start.hour < 17) or start.minute not in (0, 30):
            return False, "outside business hours (Mon-Fri 09:00-17:00, :00/:30)"
        end = start + timedelta(minutes=minutes)
        for b_s, b_e in self.busy:
            if start < b_e and end > b_s:
                return False, "conflict: slot already booked"
        self.busy.append((start, end))
        return True, start.isoformat()

    def reset(self) -> None:
        self.busy.clear()
