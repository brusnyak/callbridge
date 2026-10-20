# callbridge — SPEC

Voice-agent integration + eval harness. Vapi-style events → tool server →
Calendar + CRM → log → dashboard. Headline is integration + measured
reliability, not the persona.

## Scope

In: webhook receiver with HMAC auth + idempotency, calendar booking
(fake in-memory + documented-not-executed Google adapter), CRM lead
write (SQLite + signed outbound webhook), dashboard (HTML + JSON),
30-scenario eval suite with scoreboard + negative control, CI.

Out: real Vapi/Twilio calls, real Google OAuth execution, real CRM
delivery, frontend framework, auth beyond shared-secret HMAC.

## Endpoints

- `POST /vapi/webhook` — Vapi-style server events. Header
  `X-Callbridge-Signature: sha256=<hex hmac of raw body>` with secret
  from env `CALLBRIDGE_SECRET`. Rejects 401 on bad/missing signature.
  Idempotent on `event.id` (stored in `processed_events`; retries
  return the original result, no duplicate bookings).
  - `tool-calls`: `{event:{id,type:"tool-calls"}, tool:{name, call_id},
    args:{...}}` → `check_availability` returns free slots;
    `book_appointment` books or returns conflict with alternatives.
  - `end-of-call-report`: `{event:{id,type:"end-of-call-report"},
    call:{id, caller, started_at}, transcript, durations,
    latencies_ms:[...]}` → stores call row, writes lead, fires CRM
    webhook (best-effort).
- `GET /calls` — HTML table: time, caller hash, outcome, booking,
  p50/p95 latency. Plain HTML/CSS, no CDN.
- `GET /api/calls` — same rows as JSON.
- `GET /healthz` — liveness.

## Data model (SQLite)

- `calls(id, caller_hash, started_at, outcome, booking_id, p50_ms,
  p95_ms, transcript_len, created_at)`
- `bookings(id, event_id UNIQUE, call_id, start, end, tz, status)`
- `leads(id, call_id, name, phone_hash, note, created_at)`
- `processed_events(event_id PK, result_json, created_at)`

Caller PII is never stored raw — SHA256 hash only (first 12 hex chars
shown on dashboard). Timezone: Europe/Bratislava.

## Eval metrics

Per scenario: task success (expected outcome reached), tool-argument
correctness (right tool + valid args), guardrail pass (injection /
off-topic / hostile handled safely), duplicate protection (retry of
same event id causes no second booking), latency p50/p95 from recorded
`latencies_ms`. Scoreboard = pass counts + rates per category.
Negative-control mode injects known faults (wrong tool args, skipped
signature, duplicate replay counted as new) and MUST score <100% —
proves the runner can fail.
