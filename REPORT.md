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
- CI workflow written but never run on GitHub. Eval scenarios are scripted by the same side that wrote the app: they show regression protection, not real-call quality. Latency numbers are recorded inputs, not measured.
