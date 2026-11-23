# How a check works

```
 init: fetch once ──► infer questions ──► write config ──► record baseline
                                                              │
 check: fetch (fixture or URL)                                │
        │                                                     ▼
        ├── deterministic rules ◄── baseline fingerprint (paths, types, counts, stats)
        │
        └── sampled state ──► one Kev request ──► calibrated probabilities
                                   │
                                   └── fail_if conditions ──► pass/fail
        │
        ▼
 SQLite snapshot + run row → alert on the pass→fail transition
```

## Starting from a payload

`sentinel init` fetches the endpoint once, stores that response as the baseline,
and runs `sentinel/suggest.py` over the JSON. The inference is heuristic and
conservative; it only proposes a question when a concrete field makes the
question meaningful:

| Field pattern | Proposed question |
|---|---|
| a currency code field (`currency: USD`) | choice: which currency are the amounts in? |
| `posted_at`, `published_at`, `created_at`, `updated_at` | yes/no: were these published/updated in 2026? |
| dates only in metadata (`generated_at`, `checked_at`, `date`) | yes/no: do the dates in this payload fall in 2026? |
| a top-level or low-cardinality status field | choice: what is the overall status? |
| numeric series with a `*_units` map | yes/no: are the values within the observed range and stated unit? |
| temperature fields plus a `units` field | yes/no: are the temperatures in the stated range and unit? |
| a long-text field (`description`, `summary`, …) | yes/no: do these describe the item, rather than advertising? (jobs/news wordings) |

Every suggestion carries a rationale naming the field and what a silent change
would look like. Defaults are labelled, medium-confidence suggestions are marked
`(verify)` in the CLI output, and `sentinel calibrate` is what replaces the
defaults with measured thresholds. `sentinel suggest` re-runs inference without
rewriting the config.

## Exact-value watches

Some fields should never be judged by a model: a per-token price, a plan limit, a
currency code, a version string. A `watch` block records those fields at baseline
time (inside the fingerprint) and reports any change beyond an optional relative
tolerance:

```yaml
watch:
  key: id                               # identify the record list by this field
  fields: [pricing.prompt, pricing.completion]
  tolerance: 0.01                       # 1% relative; 0 is exact
  report_removed: false                 # whether a disappeared entry counts
```

This is the deterministic counterpart to a semantic question. The rule of thumb:
**watch it if you can name the exact field; ask Kev if you can only describe the
failure.** A rules-only endpoint (`questions: {}`) needs no Kev server at all,
which makes exact checks cheap enough for a cron job every few minutes.

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

For a `text/event-stream` response, the parsed event list
(`{"events": [{"event": ..., "data": ...}]}`) is the payload. SSE comment lines
such as `: ping` are dropped, `data:` values that parse as JSON stay objects, and
collection stops at `stream_timeout_s`, 256 KB, or a done/complete event.

Sampling is a deliberate trade: 100 records rarely change a judgement, and every
token is latency. If your endpoint needs full fidelity, raise `sample_records`
and `max_state_chars` and re-run `calibrate`.

Kev-0.8B itself is not the constraint. The Kev 1.0 model card documents states of
up to 65,536 tokens (accuracy validated to 8,192; training states were at most
7,552) and about 3.8 GB of resident GPU memory on an L4. A longer state costs
prefill time — roughly 1.4 s for 8k tokens and 21 s for 65k on an M5 — and the
prefix cache only helps within one request's set of questions, because the next
check usually sends a different payload.

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
  sample, watch, or ask about is invisible.
- Watches compare exact values (with an optional relative tolerance). They will
  not tell you whether a changed price is a good deal, only that it changed.
- Question inference is a starting point. It cannot know that `price` is a
  subscription price or that `status` means deployment status; a human confirms
  the proposals in review, and `calibrate` confirms them with numbers.
- Semantic judgement quality depends on the question and the checkpoint. Always
  run `sentinel chaos` after changing questions; a green check alone only means
  the questions did not object.
- Fixtures are recorded bodies, not mocks. They are for examples and CI; production
  checks should use `--live`.
- Range questions catch conversions that leave the observed range (Celsius
  values relabelled as Fahrenheit) and scale regressions; they do not catch a
  unit change whose values still fall inside the range (wind in m/s inside a
  km/h range). Add a domain question if that matters.
- For event streams, the inferred `stream_reply` question detects a missing reply
  (Kev-0.8B: 0.62 for a reply, 0.45 for a handshake-only stream), not a wrong one.
  Add an endpoint-specific question about the reply content if that matters.
- Kev-0.8B is the cheap default and its calibration is narrower than Kev-4B/9B.
  Content-authenticity questions separate less cleanly on it; see the model table
  in the Kev README for what each checkpoint is good at.
- The smallest useful checkpoint is 0.8B. The superseded 0.5B prototype was
  measured (see [bench/results/model-comparison.md](../bench/results/model-comparison.md)):
  it is slower on Apple Silicon and less accurate. There is no 0.4B release.

## Attribution

The model, serving code and weights are the Kev project's (Apache-2.0). This tool
is independent; see `NOTICE`.