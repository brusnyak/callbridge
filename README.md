# callbridge

A tool server for voice agents (Vapi-style): signed webhook, calendar booking, CRM lead write with a signed outbound webhook, call dashboard, and a 30-scenario eval suite with a negative control. Spec: SPEC.md. Verification evidence and gaps: REPORT.md.

## What is real vs simulated

**Real, and covered by the tests and the eval suite:**

- HMAC signature check (`hmac.compare_digest`, constant time). A bad or missing signature is rejected with 401; an unset secret answers 503 (fail closed).
- Idempotency on `event.id`: a retry returns the first result with `duplicate: true`, and no second booking is written.
- Double-booking conflict check, tool-argument validation, off-topic/hostile guardrails, HTML escaping on the dashboard.
- The unit suite (`pytest -q`) and the 30-scenario eval suite with a 6-fault negative control.

**Simulated, fake or stubbed — read this before trusting anything above:**

- The calendar is a **fake in-memory implementation** (`app/calendar_fake.py`): fixed Mon–Fri 09:00–17:00 Europe/Bratislava half-hour slots and conflict detection kept in a Python list. Nothing is written to Google, Outlook, Exchange or any real calendar.
- The Google Calendar adapter (`app/calendar_google.py`) is a **stub**: its constructor raises `NotImplementedError`. Nothing in this repo has ever called Google.
- **No real Vapi account and no real call has ever been run.** The payload shape follows Vapi's documented tool-call format, but it is a local imitation, not a tested integration. No call has been placed, received, transcribed, spoken to or billed.
- The **eval scenarios were authored by the repo author** (`evals/scenarios.json`). They protect against regressions; they do not measure real call quality or real customer behaviour.
- **No latency is reported.** The scenario files contain recorded `latencies_ms` inputs, not measurements, so the old p50/p95 figure was removed.
- The CRM is a local table plus a best-effort signed outbound webhook. In the tests the target is a throwaway HTTP server on 127.0.0.1; **no CRM has ever received a lead.**
- Postgres is a real database, but there is no production deployment. The default store is the local SQLite one.

## Run it in 60 seconds (from a clean clone)

### Option A — Docker (app + Postgres, nothing to install)

```bash
git clone <this repo> callbridge && cd callbridge
docker compose up --build
```

`docker compose up` starts Postgres 16 and the app (STORE=postgres, non-root
container, health check on `/health`). Then, in another terminal:

```bash
# 1. health
curl -s http://127.0.0.1:8000/health
# {"status":"ok","store":"postgres","db":"ok"}

# 2. one signed webhook call (HMAC-SHA256 of the raw body)
export CALLBRIDGE_SECRET="${CALLBRIDGE_SECRET:-dev-only-change-me}"   # compose default, local dev only
BODY='{"event":{"id":"demo-1","type":"tool-calls"},"tool":{"name":"book_appointment","call_id":"demo-call","args":{"start":"2026-10-20T10:00:00+02:00"}}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$CALLBRIDGE_SECRET" | awk '{print $2}')
curl -s -X POST http://127.0.0.1:8000/vapi/webhook \
  -H 'Content-Type: application/json' \
  -H "X-Callbridge-Signature: sha256=$SIG" \
  -d "$BODY"
# {"ok":true,"duplicate":false,"result":{"ok":true,"tool":"book_appointment","booking_id":"bk_...","start":"2026-10-20T10:00:00+02:00"}}
```

Dashboard: http://127.0.0.1:8000/calls — JSON: http://127.0.0.1:8000/api/calls

The demo slot is now booked, so a second identical call returns
`"error":"conflict: slot already booked"`. Use another `start` for another try.

Shut down with `docker compose down` (add `-v` to drop the Postgres volume).

### Option B — Python only (no Docker)

```bash
git clone <this repo> callbridge && cd callbridge
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q                      # unit tests
.venv/bin/python evals/runner.py --negative        # evals + negative control, exit 0 = every fault caught
```

Start the server (optional):

```bash
export CALLBRIDGE_SECRET=change-me        # required; unset = server answers 503
export CALLBRIDGE_CRM_URL=http://127.0.0.1:9000/hook   # optional, CRM webhook target
.venv/bin/uvicorn app.main:app --port 8000
# dashboard: http://127.0.0.1:8000/calls   JSON: /api/calls
```

Sign a request: header `X-Callbridge-Signature: sha256=<hex HMAC-SHA256 of raw body with CALLBRIDGE_SECRET>`.
Idempotent on `event.id`: a retry returns the first result with `duplicate: true`.

## Environment variables

| variable | default | meaning |
|---|---|---|
| `STORE` | `memory` | Which store to use: `memory` = SQLite (`app/db.py`), `postgres` = SQLAlchemy 2.x Core store (`app/db_postgres.py`). Any other value is a startup error. |
| `DATABASE_URL` | unset | Postgres URL, required when `STORE=postgres`: `postgresql+psycopg://user:pass@host:5432/db`. |
| `CALLBRIDGE_SECRET` | unset | **HMAC-SHA256 key** for the `X-Callbridge-Signature` header. Unset means the webhook answers 503 (fail closed) — there is no built-in default secret. |
| `CALLBRIDGE_HASH_KEY` | `CALLBRIDGE_SECRET` | Separate key for the keyed hash of caller/phone numbers. Set it to keep hashes stable if the webhook secret rotates. |
| `CALLBRIDGE_DB` | `callbridge.db` | SQLite file path for `STORE=memory` (`:memory:` for an in-process database). |
| `CALLBRIDGE_CRM_URL` | unset | Target for the signed outbound CRM webhook. Unset = the lead is stored locally and nothing is sent. |

Local-dev placeholders used by `docker-compose.yml` — **not secrets, do not use them anywhere real**:
`CALLBRIDGE_SECRET=dev-only-change-me`, `POSTGRES_PASSWORD=callbridge-local-dev`,
`POSTGRES_USER=callbridge`, `POSTGRES_DB=callbridge`. Override them in your shell or in a git-ignored `.env`.

Using Postgres outside Docker (any reachable Postgres):

```bash
export STORE=postgres
export DATABASE_URL=postgresql+psycopg://callbridge:callbridge@127.0.0.1:5432/callbridge
.venv/bin/uvicorn app.main:app --port 8000
```

The four tables are created on startup (`metadata.create_all`) — there is no migration step to run.

## Negative control: 6 injected faults, all 6 caught

The eval runner breaks the app on purpose, one fault per run, and the suite must go red. A suite that stays green on a broken app proves nothing.

```bash
.venv/bin/python evals/runner.py --negative   # prints "caught" per fault, exits 0 only if all 6 are caught
.venv/bin/python evals/runner.py --fault no-idempotency   # a single fault: exits 1 (fail) by design
```

## Google Calendar adapter: OAuth steps (documented, NOT executed)

`app/calendar_google.py` is a stub that raises NotImplementedError. To wire it later:
1. Google Cloud Console: create a project, enable the Google Calendar API.
2. OAuth consent screen: External, add your Gmail as a test user.
3. Credentials: create an OAuth client ID (Desktop app), download the JSON as `credentials.json` (git-ignored).
4. Install `google-api-python-client google-auth-oauthlib` and run the installed-app flow once locally; it writes `token.json` (git-ignored).
5. Implement `check_availability(date)` (events.list / freebusy) and `book(start_iso)` (events.insert, timeZone Europe/Bratislava) to the FakeCalendar interface, then assign it to `app.main.calendar`.
Nothing in this repo has called Google.

## Scoreboard (generated by evals/runner.py, last run)

| category | pass | total |
|---|---|---|
| auth | 2 | 2 |
| availability | 5 | 5 |
| booking | 5 | 5 |
| guardrail | 3 | 3 |
| hostile | 2 | 2 |
| language | 2 | 2 |
| offtopic | 2 | 2 |
| report | 5 | 5 |
| retry | 2 | 2 |
| tool-failure | 2 | 2 |

| metric | pass | total |
|---|---|---|
| auth | 2 | 2 |
| duplicate | 2 | 2 |
| guardrail | 3 | 3 |
| tool_args | 4 | 4 |

## Negative control results (last run)

| fault | scenarios passed | caught (run failed)? |
|---|---|---|
| skip-signature | 29/30 | yes |
| no-idempotency | 28/30 | yes |
| no-conflict-check | 29/30 | yes |
| wrong-tool-args | 26/30 | yes |
| no-guardrail | 27/30 | yes |
| trust-payload-booking | 19/30 | yes |

## Failed scenarios (normal run)

- none
