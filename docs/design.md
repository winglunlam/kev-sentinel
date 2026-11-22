# How a check works

```
 fetch (fixture or URL)
        │
        ▼
 deterministic rules ◄── baseline: schema fingerprint, item count, blank ratio, size
        │
        ▼
 build state (sampled payload + endpoint identity)
        │
        ▼
 one Kev request: all questions for this endpoint
        │
        ▼
 answers → fail_if conditions → pass/fail
        │
        ▼
 SQLite snapshot + run row → alert on the pass→fail transition
```

## Two layers, on purpose

The rules layer is a schema validator with a memory. It fingerprints the baseline
JSON as paths and types (`jobs[].title: string`), then reports removed fields,
type changes, fields that went null, record-list moves, count drops, blank-string
floods, and size collapses. It is exact, fast (sub-millisecond), and blind to
meaning: a payload whose status flips from `ok` to `outage` has an identical
fingerprint.

The semantic layer asks the endpoint's questions with Kev. It sees a sampled
payload (three records by default) plus the endpoint name and HTTP metadata. Each
answer is a probability, a choice, or a score, and each question declares the
condition that fails. Answers for all questions arrive in one request because Kev
scores branches in one prefill pass; asking separately multiplies latency for no
gain (`sem.separate` exists only for debugging isolation).

## What the baseline stores

`sentinel init` / `sentinel baseline set` records the response body, its SHA-256,
the structural fingerprint, and the stats used by the size/blank rules. Baselines
live in `.sentinel/sentinel.db` next to the config, which is gitignored. The
config is the reviewable artifact; the database is local state. Re-baseline after
an intentional change and the diff in a PR shows the new questions or thresholds.

Snapshots are pruned to the most recent 200 per endpoint, and the baseline
snapshot is never pruned.

## State construction

The default template includes the endpoint name, URL, HTTP status and content
type, and the payload. Large payloads are sampled (records lists are cut to
`sample_records`, then the whole text is cut to `max_state_chars`). Override it
per endpoint with a `state` template; available placeholders are `{name} {url}
{status} {content_type} {payload} {body}`.

Sampling is a deliberate trade: 100 records rarely change a judgement, and every
token is latency. If your endpoint needs full fidelity, raise `sample_records`
and `max_state_chars` and re-run `calibrate`.

## Alerts

The default is to alert on the transition into failure, not on every failing run,
and not more often than `alert_cooldown_s` (one hour). Set `webhook_url` in the
config or `SENTINEL_WEBHOOK_URL` in the environment; the payload is JSON with a
Slack-compatible `text` field. A semantic error (Kev unreachable) is reported as
an exit code 3 and does not fire a webhook: it is an infrastructure problem, not
an endpoint drift.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | all checks passed |
| 1 | at least one check failed |
| 2 | config, baseline, or usage error |
| 3 | Kev is unreachable or rejected the request |

## Limits

- It is not a scraper. You give it a URL or a recorded body; auth, pagination and
  browser rendering are yours.
- It sees only what the state template shows. A drift in a field you do not
  sample and do not ask about is invisible.
- Semantic judgement quality depends on the question and the checkpoint. Always
  run `sentinel chaos` after changing questions; a green check alone only means
  the questions did not object.
- Fixtures are recorded bodies, not mocks. They are for examples and CI; production
  checks should use `--live`.
- Kev-0.8B is the cheap default and its calibration is narrower than Kev-4B/9B.
  Content-authenticity questions separate less cleanly on it; see the model table
  in the Kev README for what each checkpoint is good at.

## Attribution

The model, serving code and weights are the Kev project's (Apache-2.0). This tool
is independent; see `NOTICE`.
