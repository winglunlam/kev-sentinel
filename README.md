# kev-sentinel

**Uptime monitoring for meaning.** Your endpoint returns `200`, valid JSON, and the same fields — and the data is wrong.

kev-sentinel checks APIs and scrapers for two kinds of change: the ones a schema validator can see, and the ones only a model that reads the payload can. It runs a local [Kev](https://github.com/jaredpalmer/kev) checkpoint, batches every question for an endpoint into one request, and turns calibrated probabilities into pass/fail conditions you commit to git.

- **Two layers.** A structural fingerprint catches removed fields, type changes, count drops, null floods, HTML error pages, and truncated bodies. Kev catches a payload that still validates but no longer means what it meant.
- **Cheap enough to run on every endpoint, every hour.** Kev is a prefill-only pointer head: no text generation, one request per endpoint, CPU or Apple Silicon.
- **No egress.** The payload goes to a model on your machine, not to a cloud API.
- **Falsifiable.** `sentinel chaos` injects silent changes and prints which layer catches each one. `sentinel calibrate` measures clean vs drifted scores and suggests thresholds.
- **CI-native.** Exit codes, JSON output, GitHub step summaries, and a composite action.

```
✓ status-page  200  86 ms
   ✓ rules  schema unchanged · 5 items (baseline 5)
   ✓ sem    services_list: p(true)=0.96
   ✓ sem    all_operational: p(true)=0.98
   ✓ sem    reporting_outage: p(true)=0.03

✗ status-page  200  91 ms          # after `sentinel chaos --inject status-flip`
   ✓ rules  schema unchanged · 5 items (baseline 5)
   ✗ sem    all_operational: p(true)=0.02   fails `p < 0.55`
   ✗ sem    reporting_outage: p(true)=0.95  fails `p > 0.4`
```

Every field, every type, and the record count are unchanged. The status page now reports an outage, and the schema layer cannot tell.

## Why this exists

Schema checks and API monitors answer the wrong question. They tell you the endpoint is up and the shape is right. They cannot tell you that:

- a status endpoint flipped `ok` to `outage` while keeping every field;
- a price feed switched from USD to EUR while keeping the numbers in `price`;
- a forecast started returning Fahrenheit values under `"units": "metric"`;
- a news feed is serving 2019 articles with today's `fetched_at`;
- a job board's API is returning advertising text inside the `title` and `description` fields.

All five are schema-valid, all five break downstream consumers, and all five are caught by the questions in [`examples/sentinel.yaml`](examples/sentinel.yaml).

Warehouse data-quality tools (Soda, Great Expectations, dbt tests) check tables after ingestion, not a per-endpoint response before it lands. Uptime monitors check status codes and latency. JSON Schema checks structure. A cloud LLM on every check can see meaning, but the cost and latency make hourly checks on dozens of endpoints hard to justify, and shipping scraped payloads to a third party is often a non-starter. kev-sentinel is the narrow tool in between: semantic, per-endpoint, local, and cheap.

## The demo

```console
$ sentinel init jobs-api --fixture examples/fixtures/jobs-api.json
wrote sentinel.yaml
baseline for jobs-api: HTTP 200, 5 items, 2422 bytes, 19 schema paths (fixture)
data: .sentinel/sentinel.db

next:
  sentinel check jobs-api
  sentinel chaos --endpoint jobs-api

$ sentinel check status-page --config examples/sentinel.yaml
✓ status-page  200  644 ms  fixture
   ✓ rules  HTTP 200 (expected 200)
   ✓ sem    services_list: p(true)=0.96
   ✓ sem    all_operational: p(true)=0.98
   ✓ sem    reporting_outage: p(true)=0.03

$ sentinel chaos --config examples/sentinel.yaml --endpoint status-page --inject status-flip
                     chaos injection: which layer catches the change
┏━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ injector    ┃ expected  ┃ rules  ┃ Kev                                                    ┃ detected ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ status-flip │ semantics │ passed │ caught: all_operational: p(true)=0.02 fails `p < 0.55` │   yes    │
└─────────────┴───────────┴────────┴────────────────────────────────────────────────────────┴──────────┘
1/1 applicable mutations detected · 1 caught by semantics only
```

`status-flip` rewrites health words to failure states (`ok` → `outage`, `operational` → `down`) and stretches latency values. Types, keys, and counts are untouched. The rules layer passes; Kev does not.

## Install

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and a local Kev server.

### 1. Run Kev

```bash
git clone https://github.com/jaredpalmer/kev
cd kev
uv sync --extra serve          # add the MLX backend on Apple Silicon via `--extra serve`
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009
```

Kev-0.8B is the cheapest default and works on a laptop. Kev-4B is worth the extra memory when semantic accuracy matters more than latency; point `sem.model` or `SENTINEL_KEV_MODEL` at whatever checkpoint your server is running.

### 2. Install kev-sentinel

```bash
git clone <your-org>/kev-sentinel
cd kev-sentinel
uv sync --extra dev
uv run sentinel check --config examples/sentinel.yaml
```

Or install it as a tool:

```bash
uv tool install --from . kev-sentinel     # from a checkout; PyPI publishing is a follow-up
sentinel --help
```

If Kev is not on `http://127.0.0.1:8009`, set `SENTINEL_KEV_BASE_URL` or edit `sem.base_url` in `sentinel.yaml`.

## Quick start on your endpoint

```bash
sentinel init jobs-api --url https://boards.example.com/api/jobs
$EDITOR sentinel.yaml    # make the questions specific to this endpoint, set fail_if
sentinel calibrate jobs-api
sentinel check
sentinel chaos --endpoint jobs-api
```

`sentinel init` records the baseline immediately. If the URL is not reachable at setup time, record a baseline later from a real response with `sentinel baseline set jobs-api` (or point it at a recorded file with `--fixture`).

Sample config:

```yaml
version: 1

sem:
  base_url: http://127.0.0.1:8009
  model: kev-latest

endpoints:
  - name: jobs-api
    url: https://boards.example.com/api/jobs
    questions:
      job_duties:
        type: noul
        instructions: >
          Do the job descriptions mention duties, skills, or responsibilities that a
          person would perform in the role?
        fail_if: "p < 0.55"
      salary_currency:
        type: choice
        instructions: Are the listed salaries in US dollars, another currency, or unclear?
        criteria:
          usd: US dollars
          other: Another currency
          unclear: Missing, mixed, or unclear
        fail_if: "choice != 'usd'"
```

`fail_if` is the condition under which the check fails. `p < 0.55` fails when Kev's probability of "yes" drops below 0.55; `p > 0.15` fails when Kev thinks an error page is likely. Thresholds are explicit so they can be reviewed in a pull request. See [docs/questions.md](docs/questions.md) for what makes a question work with Kev.

### Configuration reference

Top level:

| Key | Default | Meaning |
|---|---|---|
| `version` | `1` | config schema version |
| `sem.base_url` | `http://127.0.0.1:8009` | Kev server; overridden by `SENTINEL_KEV_BASE_URL` |
| `sem.model` | `kev-latest` | model name sent to Kev; overridden by `SENTINEL_KEV_MODEL` |
| `sem.api_key_env` | `SENTINEL_KEV_API_KEY` | bearer token for a protected server |
| `sem.separate` | `false` | ask one question per request (debugging only) |
| `sample_records` | `3` | records kept in the state for Kev |
| `max_state_chars` | `6000` | hard cap on the state text |
| `webhook_url` | unset | JSON webhook; also `SENTINEL_WEBHOOK_URL` |
| `alert_cooldown_s` | `3600` | minimum gap between alerts per endpoint |

Per endpoint:

| Key | Default | Meaning |
|---|---|---|
| `name` | required | unique name, used in commands and storage |
| `url` | — | `http(s)://` endpoint; one of `url`/`fixture` required |
| `fixture` | — | recorded body used instead of the URL (examples, CI) |
| `method` | `GET` | HTTP method |
| `headers` | `{}` | request headers |
| `questions` | required | map of question id → question |
| `state` | built-in template | placeholders: `{name} {url} {status} {content_type} {payload} {body}` |
| `allow_extra_fields` | `true` | new fields are a warning, not a failure |
| `expected_status` | `200` | required status code |
| `expect_json` | `true` | parse and fingerprint the body as JSON |
| `min_items` | unset | minimum record count |
| `count_floor` | `0.5` | fail when records drop below this fraction of baseline |
| `timeout_s` | `15` | fetch timeout |
| `notes` | `""` | free text for humans |

Question types: `noul` (yes/no probability), `choice` (enum), `score` (ordered level). Metrics available in `fail_if`: `p` and `confidence` for choice, `score` and `confidence` for score, `p` for noul. The choice metrics are validated when the config loads.

## How it works

```
 fetch (URL or recorded fixture)
        │
        ├── structural fingerprint vs baseline ──► removed fields, type changes, null floods,
        │                                          count drops, size collapses, HTML pages
        │
        └── sampled state ──► one Kev request ──► calibrated probabilities
                                   │
                                   └── fail_if conditions ──► pass/fail
        │
        ▼
 SQLite snapshot + run → alert on the pass→fail transition
```

The baseline is structural: paths and types (`jobs[].title: string`), not values, plus stats for the size and blank-string rules. Values are Kev's job. All questions for an endpoint go into one Kev request because Kev scores branches in one prefill pass; asking three questions separately triples the latency for no accuracy gain.

Snapshots and run history live in `.sentinel/sentinel.db` next to the config (gitignored). The config is the artifact you review and share; the database is local state. Full details in [docs/design.md](docs/design.md).

## Commands

| Command | What it does |
|---|---|
| `sentinel init NAME --url URL` | write a starter config and record the baseline |
| `sentinel baseline set NAME...` | fetch endpoints and record new baselines |
| `sentinel baseline show NAME` | print the baseline's timestamp, size, schema path count, hash |
| `sentinel check [NAME...]` | run rules and Kev against the baselines |
| `sentinel chaos [--endpoint NAME] [--inject NAME]` | inject silent changes and report which layer catches each |
| `sentinel calibrate [NAME...]` | measure clean vs drifted scores, suggest `fail_if` thresholds |
| `sentinel report [--last N] [--md]` | recent runs and baseline ages |
| `sentinel doctor` | config, baselines, and Kev connectivity in one shot |

Useful flags: `--live` (ignore fixtures), `--no-sem` (rules only), `--json` (stdout only, for scripts), `--github-summary`, `--no-alert`.

Exit codes: `0` all pass, `1` check failed, `2` config/baseline problem, `3` Kev unreachable.

## Calibration

A threshold is a claim about your checkpoint and your payload, so the tool measures it instead of guessing. `sentinel calibrate` runs each question on the clean payload and on the semantic chaos injections, then suggests the midpoint between the clean score and the worst injected score.

```console
$ sentinel calibrate --config examples/sentinel.yaml jobs-api
                              calibration: clean vs injected drift
┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ endpoint ┃ question        ┃      clean ┃ worst injection       ┃ suggested fail_if ┃ margin ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ jobs-api │ job_duties      │       0.87 │ wrong-entity 0.34     │ p < 0.6           │   0.53 │
│ jobs-api │ salary_currency │ usd (0.69) │ currency-swap unclear │ choice != 'usd'   │   1.00 │
│ jobs-api │ dates_2026      │       0.84 │ stale-dates 0.38      │ p < 0.6           │   0.46 │
└──────────┴─────────────────┴────────────┴───────────────────────┴───────────────────┴────────┘
```

Suggestions are a starting point, not an oracle. A question with margin under 0.10 is printed as `no separation` and should be rewritten. Thresholds are checkpoint-specific; re-run calibration when you change the Kev checkpoint.

## Proof, not claims

`sentinel chaos` is a test suite for your detectors. Each injector is a deterministic rewrite of a real payload; the table says which layer caught it and what it said.

```console
$ sentinel chaos --config examples/sentinel.yaml --endpoint jobs-api
...
│ wrong-entity   │ semantics │ passed   │ caught: job_duties: p(true)=0.34 fails `p < 0.55`   │   yes    │
│ stale-dates    │ semantics │ passed   │ caught: dates_2026: p(true)=0.38 fails `p < 0.6`    │   yes    │
│ currency-swap  │ semantics │ passed   │ caught: salary_currency: choice=unclear (0.14)      │   yes    │
│ field-rename   │ rules     │ caught: removed field: jobs[].id │ passed                  │   yes    │
│ empty-list     │ rules     │ caught: removed field: jobs[]    │ caught: job_duties ...   │   yes    │
...
10/10 applicable mutations detected · 3 caught by semantics only · 2 not applicable
```

Injectors: `wrong-entity`, `stale-dates`, `currency-swap`, `unit-swap`, `status-flip` (semantic-only), plus `field-rename`, `null-fields`, `empty-list`, `string-numbers`, `status-500`, `html-error`, `truncated` (structural). An injector that does not apply to a payload (say, `unit-swap` on a job board) is reported as `n/a` and excluded from the assertion.

Use `--assert` in CI: it exits nonzero if any failure class goes undetected across the configured endpoints. A row that is applicable but undetected is a gap for that endpoint — add a question that covers it, or accept it explicitly.

## Benchmark

Measured 2026-11-22 on an Apple M1 (16 GB) with `jaredpalmer/kev-0.8b` served via MLX, Python 3.13, over the five example payloads. 46 drift cases, each run twice (92 drift runs) and 5 clean payloads, each run twice (10 clean runs).

| Backend | Detection | False positives | p50 per check | p95 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|---|
| rules only | 68/92 (74%) | 0/10 | 0.0 ms | 0.1 ms | 0.0 ms | $0 |
| Kev-0.8B + rules | 82/92 (89%) | 0/10 | 126.9 ms | 2432.7 ms | 459.6 ms | $0.0023 |

Cost is electricity only (60 W at $0.30/kWh) from the mean latency; hardware amortization is excluded. A cloud comparison is not published because a fair one needs the provider's current prices and a prompt adapter — `bench/bench.py --judge-cmd` runs an external judge over the same labelled set so you can add one without forking the harness.

The 10 missed drift runs are the two failure classes no question covers on the endpoints where they were injected: `wrong-entity` on endpoints without a content-authenticity question, and `stale-dates` on endpoints without a date question. That is the intended failure mode of a question-driven tool: the gap is visible, attributable, and closable with `sentinel calibrate`.

What the numbers mean:

- **Detection**: the rules-only row is the schema-validator baseline; the second row is the full pipeline. Kev adds 14 detections that structural checks cannot make, with no false positives on the clean payloads.
- **Latency** varies with state length and prefix-cache hits. p50 is the typical warm check; p95 covers long payloads whose state was not in the cache.
- **Cost** is dominated by hardware you already own; the electricity estimate is included to make the comparison with per-token APIs concrete.

Reproduce:

```bash
# with a Kev server running
uv run python bench/bench.py --config examples/sentinel.yaml --repeats 2 \
  --json bench/results/kev-0.8b.json --markdown bench/results/kev-0.8b.md
```

The committed results are in [`bench/results/`](bench/results); the methodology is in [`bench/README.md`](bench/README.md).

## CI

Composite action:

```yaml
name: sentinel
on:
  schedule: [{ cron: "17 * * * *" }]
  workflow_dispatch:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      # Kev must be reachable; run it on a self-hosted runner or point at a host
      - uses: <your-org>/kev-sentinel@v0.1.0
        with:
          config: sentinel.yaml
          kev-url: http://127.0.0.1:8009
```

Without a Kev server, set `no-sem: "true"` and the action runs the deterministic layer only. The repository's own [`.github/workflows/sentinel-example.yml`](.github/workflows/sentinel-example.yml) does exactly that on every push: it records baselines from the fixtures, checks them, and asserts that structural drift is caught — no weights, no GPU.

## Prior art and where this fits

- **Uptime and API monitors** (status code, latency, response assertions) answer "is it up?" These questions are structural by construction.
- **JSON Schema validators and contract testing** answer "is the shape right?" They are the rules layer without the baseline memory or the sampling. See [JSON Schema](https://json-schema.org).
- **Warehouse data-quality tools** ([Soda Core](https://github.com/sodadata/soda-core), [Great Expectations](https://github.com/great-expectations/great_expectations), [dbt tests](https://docs.getdbt.com/docs/build/data-tests)) answer "is this table plausible?" after ingestion, typically with SQL and structural or statistical tests. They do not see an endpoint's response before it lands, and they do not judge meaning with a model.
- **Synthetic monitoring** (scripted browser flows) catches broken pages, not changed data, and breaks on markup changes.
- **LLM-on-every-check** sees meaning but pays cloud latency, per-token cost, and egress on every check. That is the trade kev-sentinel refuses to make: a 0.8B prefill-only model, one batched request per endpoint, offline.

The niche is the combination: semantic, per-endpoint, cheap enough to run hourly, and local. If you know a tool that already does that, please open an issue.

## Limits

- It is not a scraper. Auth, pagination, and browser rendering are yours; give it a URL or a recorded body.
- It sees what the state template shows. Drift in an unsampled field you never ask about is invisible.
- Question quality is the ceiling. `sentinel chaos` and `sentinel calibrate` exist because a green check only proves the questions did not object.
- Kev-0.8B is the cheap default; content-authenticity questions separate less cleanly on it than on Kev-4B. Thresholds are per-checkpoint.
- The alert path is a JSON webhook, not a pager integration. Wire it to Slack/PagerDuty/Opsgenie on your side.

## Roadmap

- A fine-tuned Kev checkpoint trained on collected drift labels, published with its calibration report.
- A redesign detector: when the schema changes, use Kev to choose between candidate mappings instead of alerting.
- A hosted dashboard for teams that do not want to run the CLI.
- A published cloud-judge adapter and a measured cost comparison.
- A larger public labelled drift set, so detection rates are comparable across models.

Issues and pull requests are welcome. If you publish a "built with Kev" tool, tell the Kev project about it; the ecosystem list is still short.

## Development

```bash
uv sync --extra dev
uv run pytest                 # 60+ tests, no weights or network
make check                    # rules-only check over the example fixtures
make demo                     # requires a Kev server on :8009
make bench
```

Layout: `sentinel/core` (fetch, fingerprint, store, state), `sentinel/rules` (deterministic), `sentinel/sem` (Kev client and answer evaluation), `sentinel/chaos.py` (injectors), `sentinel/calibrate.py`, `sentinel/cli.py`. Tests use a fake judge, so the suite runs on any machine.

## Attribution and license

kev-sentinel is independent software, Apache-2.0. It uses the Kev model family, serving code, and API design from [jaredpalmer/kev](https://github.com/jaredpalmer/kev) (Apache-2.0), which builds on the architecture described in [Jev's Architecture Unmasked](https://archerhume.com/posts/jevs-architecture-unmasked). It is not affiliated with, endorsed by, or maintained by the Kev authors or Vercel. See [NOTICE](NOTICE).