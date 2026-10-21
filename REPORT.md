# callbridge verification report (2026-10-18)

Environment: Python 3.14.0 in `.venv` (fastapi, uvicorn, pytest, httpx from PyPI; no other installs). No network except pip. `~/.env` never read.

## Commands and outputs
```
$ .venv/bin/python -m pytest -q
26 passed, 1 warning in 0.60s

$ .venv/bin/python evals/runner.py
NORMAL: 30/30 (100.0%) p50=150.0ms p95=200.0ms dims={'tool_args': '4/4', 'duplicate': '2/2', 'guardrail': '3/3', 'auth': '2/2'}
NEGATIVE skip-signature: 29/30 -> caught
NEGATIVE no-idempotency: 28/30 -> caught
NEGATIVE no-conflict-check: 29/30 -> caught
NEGATIVE wrong-tool-args: 26/30 -> caught
NEGATIVE no-guardrail: 27/30 -> caught
NEGATIVE trust-payload-booking: 19/30 -> caught

--fault skip-signature exit=1
--fault no-idempotency exit=1
--fault no-conflict-check exit=1
--fault wrong-tool-args exit=1
--fault no-guardrail exit=1
--fault trust-payload-booking exit=1
--negative exit=0
```
Mutation sanity check: replacing `hmac.compare_digest(want, sig)` with `return True` made pytest report "3 failed, 23 passed"; restored, 26 passed.

## Defects found in the worker code and fixed (originals in *.bak-20261018)
1. main.get_secret: default "dev-secret" let anyone forge events. Now unset = 503 (fail closed). Same fallback removed in crm.post_webhook.
2. main.handle_report: "booked" outcome trusted `booking_id` from the payload. Now only a booking stored for that call counts.
3. main.calls_page: payload strings rendered unescaped (stored XSS). Now html.escape.
4. db.hash_caller: bare SHA256 truncated to 12 hex of a phone number is brute-forceable. Now keyed HMAC (CALLBRIDGE_HASH_KEY or the secret).
5. main.handle_tool_call: booking `end` was stored equal to `start`. Now start + 30 min.
6. db.percentile: default quantile method extrapolates beyond the max on small samples. Now "inclusive".
7. main.webhook / handle_report: non-object JSON and junk latency values caused 500. Now 400 / ignored.
8. crm.post_webhook: URL scheme unchecked (file:// etc). Now http(s) only.
9. evals/runner.py: "negative control" corrupted inputs and covered auth only (1/30 passing was an artefact); it did not inject faults into the app, did not score tool args, duplicate protection or latency, and the fault patches would leak between runs. Rewritten: 6 faults injected into the app (skip-signature, no-idempotency, no-conflict-check, wrong-tool-args, no-guardrail, trust-payload-booking), each must fail.
10. scenarios.json: happy-report-booked relied on the forged booking_id; now reports for call c1, which has a real booking.
Checked and OK: constant-time compare (hmac.compare_digest), idempotency on event.id (INSERT OR IGNORE, cached result), Europe/Bratislava handling incl. DST and UTC-offset conflicts (tests), injection text only matched and stored as data, never executed.

## Not verified / known gaps
- Nothing run against real Vapi (payload shapes are Vapi-style, not checked against Vapi docs) or real Google Calendar; no keys used.
- calendar_google.py is a stub; OAuth steps are documentation only.
- Single-process SQLite, no concurrency test: two simultaneous deliveries of the same event.id could race past the cache (booking UNIQUE(event_id) is the backstop for bookings; leads could duplicate).
- Lead `note` stores the first 500 chars of the transcript raw, which may contain PII; lead `name` is stored raw by design.
- Injection guard is a regex list; novel phrasings pass (scored only on the 3 scripted cases).
- CI workflow runs on GitHub Actions (last two runs on 2026-10-21 succeeded). Eval scenarios are scripted by the same side that wrote the app: they show regression protection, not real-call quality. Latency numbers are recorded inputs, not measured.

## 2026-10-21: packaging pass (Docker, Postgres store, CI gate, pinned deps)

Machine: Mac (arm64), local venvs on Python 3.13.2; **no Python 3.12 on this
machine**, so CI/Docker parity was checked with a `python:3.12-slim` container.
Docker: colima VM (Ubuntu 24.04, Docker 29.2.1).

Commands and outputs:

```
$ pip install -r requirements.txt -r requirements-dev.txt      # clean venv, pinned
$ python -m pytest -q
26 passed, 4 skipped, 1 warning in 2.05s
$ python evals/runner.py
NORMAL: 30/30 (100.0%) dims={'tool_args': '4/4', 'duplicate': '2/2', 'guardrail': '3/3', 'auth': '2/2'}
NEGATIVE skip-signature: 29/30 -> caught
NEGATIVE no-idempotency: 28/30 -> caught
NEGATIVE no-conflict-check: 29/30 -> caught
NEGATIVE wrong-tool-args: 26/30 -> caught
NEGATIVE no-guardrail: 27/30 -> caught
NEGATIVE trust-payload-booking: 19/30 -> caught
$ ruff check .
All checks passed!

$ docker compose up -d --build          # app + postgres:16, both "healthy"
$ curl -s http://127.0.0.1:8000/health
{"status":"ok","store":"postgres","db":"ok"}
$ curl -s -X POST http://127.0.0.1:8000/vapi/webhook -H 'Content-Type: application/json' \
    -H "X-Callbridge-Signature: sha256=$SIG" -d "$BODY"
{"ok":true,"duplicate":false,"result":{"ok":true,"tool":"book_appointment","booking_id":"bk_7c05c32b","start":"2026-10-20T10:00:00+02:00"}}
# replay -> {"duplicate":true, same booking_id}; same slot, new event -> "conflict: slot already booked";
# bad signature -> 401
```

CI mirror (python:3.12-slim, exactly the ci.yml steps):
`ruff check .` -> All checks passed; `pytest -q` -> 26 passed, 4 skipped;
eval gate -> "scenarios passed: 30/30, faults caught: 6/6" (exit 0).
With `DATABASE_URL` set to the compose DB: `pytest -q` -> **30 passed** (26 unit + 4 Postgres).

Defect found and fixed while verifying against a live Postgres (never visible on SQLite):
- `app/db_postgres.py` `save_booking` used `result.rowcount > 0` to detect a
  duplicate `event_id`. psycopg reports **rowcount = -1** for
  `INSERT ... ON CONFLICT DO NOTHING`, so every Postgres booking returned
  "duplicate booking blocked" even though the row was written. Fixed with
  `.returning(bookings.c.id)` and checking whether a row came back.

### Not verified / known gaps (2026-10-21)
- The GitHub Actions workflow has since run on github.com: runs 39591933811 and 39592414333 (2026-10-21) both succeeded. The same steps were
  run locally in a `python:3.12-slim` container, which is not the same thing.
- The Postgres path is verified against `postgres:16` in a local colima VM only.
  No managed Postgres, no backups, no failover, no concurrent-writer test.
- The Postgres store has no migration story: tables are created with
  `metadata.create_all()` on startup, so a schema change needs a manual step.
- `docker compose` was verified on colima/Docker 29.2.1 on arm64 only; not on
  Docker Desktop, not on x86_64.
- The image was built and run as a non-root user (uid 10001); no image scan,
  no SBOM, no signed image.
- Everything in "What is real vs simulated" (README) still holds: no real
  Vapi, no real call, no real Google Calendar, no real CRM.

