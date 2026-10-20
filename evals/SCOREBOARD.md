# callbridge eval scoreboard

**Task success: 30/30 (100.0%)**

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

(Latency is not reported: the scenario files carry recorded numbers, nothing is measured.)

## Negative control (one fault injected into the app per run; must FAIL)

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
