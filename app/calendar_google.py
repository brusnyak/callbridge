"""Google Calendar adapter — WRITTEN BUT NOT EXECUTED.

Why not executed: needs real OAuth credentials + network. This file is the
integration contract so wiring it later is mechanical.

OAuth steps (see README):
  1. Google Cloud Console → new project → enable Calendar API.
  2. OAuth consent screen → External → add test user (your Gmail).
  3. Credentials → OAuth client ID (Desktop app) → download JSON.
  4. First run locally: google-auth-oauthlib flow → token.json cached.
  5. This adapter reads token.json and calls calendar.events insert/list.

Interface matches FakeCalendar: check_availability(date) -> slots,
book(start_iso) -> (ok, info).
"""
from __future__ import annotations


class GoogleCalendarAdapter:
    CALENDAR_ID = "primary"
    TIMEZONE = "Europe/Bratislava"

    def __init__(self, token_file: str = "token.json"):
        self.token_file = token_file
        raise NotImplementedError(
            "Google adapter is documented, not executed. "
            "Follow README OAuth steps, then implement with "
            "google-api-python-client using the FakeCalendar interface."
        )

    def check_availability(self, date: str) -> list[dict]:  # pragma: no cover
        raise NotImplementedError

    def book(self, start_iso: str, minutes: int = 30):  # pragma: no cover
        raise NotImplementedError
