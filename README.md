# kev-sentinel

**Uptime monitoring for meaning.** Your endpoint returns `200`, valid JSON, and the same fields — and the data is wrong.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-0a0a0a)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-0a0a0a)](pyproject.toml)
[![Model: Kev](https://img.shields.io/badge/model-Kev--0.8B-0a0a0a)](https://github.com/jaredpalmer/kev)

kev-sentinel checks APIs and scrapers for two kinds of change: the ones a schema validator can see, and the ones only a model that reads the payload can. It runs a local [Kev](https://github.com/jaredpalmer/kev) checkpoint, batches every question for an endpoint into one request, and turns calibrated probabilities into pass/fail conditions you commit to git.

- **Two layers.** A structural fingerprint catches removed fields, type changes, count drops, null floods, HTML error pages, and truncated bodies. Kev catches a payload that still validates but no longer means what it meant.
- **Cheap enough to run on every endpoint, every hour.** Kev is a prefill-only pointer head: no text generation, one request per endpoint, CPU or Apple Silicon.
- **No egress.** The payload goes to a model on your machine, not to a cloud API.
- **Starts from your payload, not a blank file.** `sentinel init` inspects the baseline and proposes questions for the currency, date, status, units, and text fields it can see, each with a rationale. `sentinel calibrate` replaces the defaults with measured thresholds.
- **Falsifiable.** `sentinel chaos` injects silent changes and prints which layer catches each one. `sentinel demo` does the whole story in one command.
- **CI-native.** Exit codes, JSON output, GitHub step summaries, and a composite action.

**Contents:** [Try the model](#try-the-model-the-reason-this-repo-exists) · [Rules example](#try-the-rules-layer-in-10-seconds) · [Questions](#do-i-have-to-write-the-yaml) · [Production readiness](#is-it-production-ready) · [Price watch](#start-here-watch-llm-model-prices) · [Kev checks](#where-kev-is-essential-llm-answer-quality) · [Install](#install) · [Configuration](#configuration-reference) · [CLI](#commands) · [Calibration](#calibration) · [Benchmark](#benchmark)

## Try the model (the reason this repo exists)

Kev exists to catch the failure a schema check cannot see: an LLM reply that
becomes a refusal, boilerplate, or an off-topic answer while HTTP status, JSON
shape and token counts stay identical. The first run takes about five minutes
because it downloads a 1.7 GB checkpoint; after that it is seconds.

```bash
git clone <your-org>/kev-sentinel     # replace <your-org> with your fork
cd kev-sentinel
uv sync --extra dev
```

**Terminal 1** — start Kev-0.8B. `make kev` clones Kev into `.kev/` (once) and
serves it on `:8009`:

```bash
make kev
```

**Terminal 2** — record a baseline, check the clean payload, inject a refusal,
and see which layer catches it:

```bash
uv run sentinel demo --config examples/agent-quality.yaml --inject refusal
```

```console
1. baseline

2. check the clean payloads
✓ support-agent  200  1125 ms  fixture
   ✓ rules  HTTP 200 (expected 200)
   ✓ sem    answers_question: p(true)=0.86
   ✓ sem    is_refusal: p(true)=0.14
   ✓ sem    on_topic: p(true)=0.76

3. inject a silent change
┏━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ injector ┃ expected  ┃ rules  ┃ Kev                                                    ┃ detected ┃
┡━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ refusal  │ semantics │ passed │ caught: answers_question: p(true)=0.25 fails `p < 0.55` │   yes    │
└──────────┴───────────┴────────┴────────────────────────────────────────────────────────┴──────────┘

verdict: schema unchanged; Kev caught it (answers_question: p(true)=0.25 fails `p < 0.55`).
```

The injected refusal has the same length as the original reply, so every
structural check passes. Only the model sees it.

If `:8009` is taken, run `make kev KEV_PORT=8010` and prefix the second command
with `SENTINEL_KEV_BASE_URL=http://127.0.0.1:8010`. Without `make`, clone
`https://github.com/jaredpalmer/kev` into `.kev/` and run
`uv sync --extra serve && uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009`.

## Try the rules layer in 10 seconds

If you do not want a model in the loop, the exact-value layer needs no Kev, no
API key, and no network beyond a recorded fixture:

```bash
uv run sentinel baseline set --config examples/llm-prices.yaml openrouter-models
uv run sentinel check --config examples/llm-prices.yaml
```

```console
✓ openrouter-models  200  1 ms  fixture
   ✓ rules  HTTP 200 (expected 200)
   – sem   skipped: no semantic questions configured

1/1 endpoint(s) ok
```

That is an exact-value watch over 24 recorded LLM prices; change one and the
check fails. The sections below go deeper on both layers.

## Do I have to write the YAML?

No. Question generation has three sources, in order of reliability:

1. **OpenAPI import** for documented APIs. The spec declares fields, formats,
   enums, bounds and often example responses; the importer turns them into
   endpoints, watches and questions without guessing from a payload.
2. **Question packs** for common endpoint archetypes (LLM agent, marketplace
   listings, news feeds). Packs are YAML data, not Python — the community can
   add and improve them.
3. **Payload inference** as the fallback for undocumented APIs: `sentinel init`
   fetches once and proposes questions for currency, dates, status, unit series,
   long text and streams, with a one-line reason each.

Then `--calibrate` measures every question against injected drift and writes the
thresholds:

```console
$ sentinel init --calibrate my-api --url https://api.example.com/items
...
calibrating thresholds (fetches the endpoint once per injection)
  description_content: p < 0.55 (p(true)=0.95 -> worst wrong-entity 0.20)
  published_2026: p < 0.5 (p(true)=0.90 -> worst stale-dates 0.10)
updated sentinel.yaml with measured thresholds
```

You hand-write a question only when the judgement is domain-specific ("does the
reply answer *this* customer?", "is this still the Berlin feed?"). Thresholds are
per checkpoint, so re-run `calibrate` when you change `sem.model`.

### From OpenAPI

```bash
uv run sentinel import openapi https://api.example.com/openapi.json --out sentinel.yaml
uv run sentinel baseline set --config sentinel.yaml
uv run sentinel preflight --config sentinel.yaml
```

Add `--fetch` (and `--api-header` for auth) to call each endpoint once and infer
from the live response instead of the spec examples:

```bash
uv run sentinel import openapi https://api.example.com/openapi.json --fetch \
  --api-header 'authorization: Bearer …' --out sentinel.yaml
```

Example responses in the spec produce payload-inferred questions and exact
watches (`pricing.prompt`, `price`, …). Operations with only a schema produce
schema-derived questions (dates, numeric ranges, currency). Operations whose path
parameters or POST body have no example are skipped with a note instead of
producing a config that cannot be called.

### From a pack

```bash
uv run sentinel pack list
uv run sentinel pack show agent
uv run sentinel init support-agent --url https://agent.example.com/chat --pack agent
```

Packs live in [sentinel/packs/](sentinel/packs/) as YAML and support a `{year}`
placeholder. Adding one is a YAML file plus a test — see
[CONTRIBUTING.md](CONTRIBUTING.md).

### LLM-assisted drafting (optional)

When there is no spec, no matching pack, and the payload inference is too generic,
ask an OpenAI-compatible model to draft questions. It is opt-in, sends one sample
payload (no headers or secrets), and validates every returned question with the
same parser:

```bash
export SENTINEL_DRAFT_API_KEY=...        # or OPENAI_API_KEY
export SENTINEL_DRAFT_MODEL=gpt-4o-mini  # optional
export SENTINEL_DRAFT_BASE_URL=https://api.openai.com/v1   # or a local gateway
uv run sentinel init support-agent --url https://agent.example.com/chat --llm-draft
```

Drafts are still drafts: `calibrate`, `chaos` and `preflight` decide whether they
are worth keeping. Keep exact values in `watch`; use a drafting model only for
judgement questions.

### From the payload

For undocumented APIs, inference remains the bootstrap:

```bash
uv run sentinel init --calibrate my-api --url https://api.example.com/items
uv run sentinel chaos --endpoint my-api
uv run sentinel preflight --config sentinel.yaml
uv run sentinel schedule --config sentinel.yaml --every 1h
```

## Is it production-ready?

The tool is; the generated config is a draft until you gate it. The maturity
ladder is four commands:

```bash
sentinel init --calibrate my-api --url https://api.example.com/items   # draft + thresholds
sentinel chaos --endpoint my-api                                       # prove the injectors are caught
sentinel preflight --config sentinel.yaml                              # GO / NO-GO gate
sentinel schedule --config sentinel.yaml --every 1h                    # cron/systemd/GitHub
```

`sentinel preflight` does not just run the check once. It verifies the baseline
exists and matches the current payload, the clean payload passes, every question
separated clean from injected drift with at least a 0.15 margin (weak and
unproven questions are listed by name), and every chaos injection class was
caught by some layer. It exits 0 only on GO, and 1 on NO-GO — so you can run it
as the last CI step before enabling cron.

It also reports a baseline older than `baseline_max_age_days` (default 30) so a
schedule does not quietly compare against a year-old definition.

That gate is the honest answer to "can I trust inferred questions?": inference
drafts, calibration measures, chaos proves coverage, and `preflight` refuses to
call it ready when any of that is missing. Questions about domain-specific
judgement still need a human; everything above tells you which ones those are.

## Kev is the engine; this repo is the contract around it

Kev answers questions about one payload. It does not know the baseline, which
fields must be exact, what to ask, what probability should alert, whether the
checks catch drift, or how to run unattended. Those are the parts this repo adds:
a recorded baseline, a rules layer for what is exact, question inference and
review, calibrated thresholds, chaos-style coverage tests, alerts, history and
CI ergonomics. [docs/why-not-just-kev.md](docs/why-not-just-kev.md) has the
side-by-side comparison, including when a 20-line script around Kev is the right
answer.

## Start here: watch LLM model prices

Providers change model prices; your cost spreadsheet, router config or budget alert does not notice. This example is complete and runs against a public endpoint. It needs **no model**: prices are exact values, so a `watch` block compares them. Use Kev questions on the same endpoint when the failure is a judgement call rather than a number.

[examples/llm-prices.yaml](examples/llm-prices.yaml):

```yaml
version: 1
endpoints:
  - name: openrouter-models
    url: https://openrouter.ai/api/v1/models
    watch:
      key: id
      fields: [pricing.prompt, pricing.completion]
    questions: {}
```

```console
$ sentinel baseline set --config examples/llm-prices.yaml openrouter-models --live
baseline for openrouter-models: HTTP 200, 477 items, 927076 bytes (url)

$ sentinel check --config examples/llm-prices.yaml --live
✓ openrouter-models  200  98 ms  url
   ✓ rules  HTTP 200 (expected 200)
   – sem   skipped: no semantic questions configured

1/1 endpoint(s) ok
```

When a price moves, the watch rule is what fires (output below is the recorded
fixture with one price changed):

```console
✗ openrouter-models  200  1 ms  fixture
   ✓ rules  HTTP 200 (expected 200)
   ✗ rules  1 watched value(s) changed
          thinkingmachines/inkling-2-mini pricing.prompt: '0.000000075' -> '0.0000002'
   – sem   skipped: no semantic questions configured

0/1 endpoint(s) ok
```

The committed example uses a recorded response by default, so the commands run
offline without `--live`. Watched values are stored in the baseline, so a price
change is an exact comparison, not a model guess. `tolerance: 0.01` would report
only changes above 1%; `report_removed: true` also reports models disappearing.

### What to do when it fires

1. **Alert.** Add `webhook_url: ${SENTINEL_WEBHOOK_URL}` to the config (or export
   `SENTINEL_WEBHOOK_URL`). Alerts fire on the pass→fail transition, not every
   run, with a one-hour cooldown. Any Slack/Teams/Opsgenie-compatible webhook
   works.
2. **Schedule it.** This config needs no Kev server, so a plain cron job is enough:

   ```cron
   17 * * * * cd /path/to/kev-sentinel &&      .venv/bin/sentinel check --config examples/llm-prices.yaml --live >> /tmp/llm-prices.log 2>&1
   ```

3. **Act on the exit code.** `check` exits 1 on a change, so a script can open an
   issue, update a cost sheet, or block a deploy:

   ```bash
   if ! sentinel check --config examples/llm-prices.yaml --live --json > /tmp/check.json; then
     gh issue create --title "LLM price change detected"        --body "$(cat /tmp/check.json)"
   fi
   ```

4. **Gate CI.** The same command in a workflow step fails the build on a change;
   `--github-summary` writes the table to `$GITHUB_STEP_SUMMARY`.

If your failure is fuzzier ("is this still a model catalogue, or an error page
that happens to be JSON?"), add a Kev question to the same endpoint:

```yaml
questions:
  catalogue:
    type: noul
    instructions: Does this payload still list models with per-token pricing?
    fail_if: "p < 0.6"
```

Then run `sentinel calibrate --config ...` to measure the threshold, and
`sentinel chaos --inject price-change` to prove the watch layer catches the exact
case that matters. Full example: [examples/llm-prices.yaml](examples/llm-prices.yaml).

## Where Kev is essential: LLM answer quality

Price watching is deterministic on purpose. This is the failure it cannot cover: an LLM support agent that starts refusing, deflecting with boilerplate, or answering a different question. HTTP status, schema, token counts and event shape can all stay identical.

[examples/agent-quality.yaml](examples/agent-quality.yaml) asks three questions about a recorded chat completion:

```yaml
questions:
  answers_question:
    type: noul
    instructions: >
      Does the reply actually answer the user's question about building a bot,
      rather than declining, deflecting, or answering something else?
    fail_if: "p < 0.55"
  is_refusal:
    type: noul
    instructions: Does the reply refuse the request, or say that it cannot help?
    fail_if: "p > 0.5"
  on_topic:
    type: noul
    instructions: >
      Is the reply about building a bot, rather than a generic support
      acknowledgement or a different topic?
    fail_if: "p < 0.65"
```

Clean:

```console
✓ support-agent  200  635 ms  fixture
   ✓ rules  HTTP 200 (expected 200)
   ✓ sem    answers_question: p(true)=0.86
   ✓ sem    is_refusal: p(true)=0.14
   ✓ sem    on_topic: p(true)=0.76
```

After `sentinel chaos --inject refusal` (the reply text is replaced by a refusal of the same length, so the rules layer still passes):

```console
✗ support-agent  200  1 ms  fixture
   ✓ rules  HTTP 200 (expected 200)
   ✗ sem    answers_question: p(true)=0.25 fails `p < 0.55`
   ✗ sem    is_refusal: p(true)=0.88 fails `p > 0.5`
   ✗ sem    on_topic: p(true)=0.51 fails `p < 0.65`
```

### Run it on a schedule

`sentinel schedule` prints a ready-to-paste cron line, systemd unit, GitHub
Actions job and long-running form for the current config. Add `--lock` so
overlapping cron runs are skipped instead of stacking up:

```console
$ uv run sentinel schedule --config examples/agent-quality.yaml --every 1h

# cron
17 * * * * cd /path/to/kev-sentinel && SENTINEL_KEV_BASE_URL=http://127.0.0.1:8009 /path/to/.venv/bin/python -m sentinel check --config /path/to/examples/agent-quality.yaml >> /tmp/kev-sentinel.log 2>&1
...
# watch
SENTINEL_KEV_BASE_URL=http://127.0.0.1:8009 /path/to/.venv/bin/python -m sentinel watch --config /path/to/examples/agent-quality.yaml --interval 3600
```

`--every` accepts `1m, 5m, 10m, 15m, 30m, 1h, 6h, 12h, 1d`, and `--format`
selects one form. Two modes:

- **one-shot** (cron/systemd/GitHub): one check per interval, exits 1 on drift.
- **`sentinel watch`**: a long-running process that checks every `--interval`
  seconds and alerts on the pass→fail transition. Easier in a container or
  service manager; `Ctrl-C` stops it. `--max-cycles` exists for tests.

Kev must be reachable for semantic checks; the rules-only price config needs no
server. Export `SENTINEL_WEBHOOK_URL` (or set `webhook_url`) and failures open a
Slack/Teams/Opsgenie incident with a one-hour cooldown — not on every run.

```cron
# rules-only, no model server needed
17 * * * * cd /path/to/kev-sentinel && .venv/bin/sentinel check --config examples/llm-prices.yaml --live >> /tmp/llm-prices.log 2>&1
```

### Where this fits in the real world

- **Third-party content feeds** — a marketplace, job board or news API that keeps its schema but starts serving spam, boilerplate, or another resource. You can describe "these are still listings"; you cannot enumerate every way they stop being listings.
- **LLM and agent canaries** — hourly probe prompts against staging or production: does the assistant still answer, refuse less, stay on topic, include the required step? Model or prompt swaps often pass every structural check.
- **Vendor document semantics** — does the feed still contain the coverage limit, the invoice total, the cancellation terms? The document is valid JSON either way.
- **Label and data pipelines** — ask the same question per row ("does this review express a complaint?") and compare the answer distribution to the baseline when a labelling vendor or prompt changes.

The rule of thumb: **if you can write the exact assertion, use `watch`; the model is for the failures you can only describe.** The rest of this README is about making those judgements cheap, calibrated and falsifiable.

## Why this exists

Schema checks and API monitors answer the wrong question. They tell you the endpoint is up and the shape is right. They cannot tell you that:

- a status endpoint flipped `ok` to `outage` while keeping every field;
- a price feed switched from USD to EUR while keeping the numbers in `price`;
- a forecast started returning Fahrenheit values under `"units": "metric"`;
- a news feed is serving 2019 articles with today's `fetched_at`;
- a job board's API is returning advertising text inside the `title` and `description` fields.

All five are schema-valid, all five break downstream consumers, and all five are caught by the questions in [`examples/sentinel.yaml`](examples/sentinel.yaml).

Warehouse data-quality tools (Soda, Great Expectations, dbt tests) check tables after ingestion, not a per-endpoint response before it lands. Uptime monitors check status codes and latency. JSON Schema checks structure. A cloud LLM on every check can see meaning, but the cost and latency make hourly checks on dozens of endpoints hard to justify, and shipping scraped payloads to a third party is often a non-starter. kev-sentinel is the narrow tool in between: semantic, per-endpoint, local, and cheap.

## Install

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and a local Kev server.

### 1. Run Kev

The shortest path is `make kev` in this repository after cloning it; the manual
steps are below.

```bash
git clone https://github.com/jaredpalmer/kev
cd kev
uv sync --extra serve          # add the MLX backend on Apple Silicon via `--extra serve`
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009
```

Kev-0.8B is the cheapest default: it fits a 4 GB GPU (an L4 is enough) or any Apple Silicon Mac. Kev-4B is worth the extra memory when semantic accuracy matters more than latency; point `sem.model` or `SENTINEL_KEV_MODEL` at whatever checkpoint your server is running.

### 2. Install kev-sentinel

```bash
git clone <your-org>/kev-sentinel     # replace <your-org> with your fork
cd kev-sentinel
uv sync --extra dev

# semantic example: needs the Kev server from step 1
uv run sentinel baseline set --config examples/agent-quality.yaml support-agent
uv run sentinel check --config examples/agent-quality.yaml
```

For a quick check without Kev:
`uv run sentinel baseline set --config examples/llm-prices.yaml openrouter-models && uv run sentinel check --config examples/llm-prices.yaml`.

Or install it as a tool:

```bash
uv tool install --from . kev-sentinel     # from a checkout; PyPI publishing is a follow-up
sentinel --help
```

If Kev is not on `http://127.0.0.1:8009`, set `SENTINEL_KEV_BASE_URL` or edit `sem.base_url` in `sentinel.yaml`.

A second example uses a real public API: [examples/open-meteo.yaml](examples/open-meteo.yaml) monitors an Open-Meteo forecast, whose parallel arrays and `*_units` maps are what the series inference is for. It runs offline against a recorded response.

## Serving and hardware

| | |
|---|---|
| Kev-0.8B server | [Kev-0.8B](https://huggingface.co/jaredpalmer/kev-0.8b) is Apache-2.0 on `Qwen3.5-0.8B-Base`. The Kev 1.0 model card measures **3.8 GB resident GPU memory** on an NVIDIA L4 (any ~4 GB GPU works) and ships an MLX path for any Apple Silicon Mac. CPU works, slower. |
| sentinel | Python 3.11+; no GPU, no model weights; no network egress beyond the endpoint you configure and an optional webhook. |
| Context | Kev-0.8B serves states of up to **65,536 tokens** plus at least 8,192 tokens per question. Accuracy is validated to 8,192 tokens (training states were at most 7,552), so longer states are served but outside the validated length. |
| State budget | sentinel samples record lists to `sample_records` and caps the state at `max_state_chars` (6,000 characters by default, roughly 1.5k tokens). That is a latency choice, not a model limit: raise both if a judgement needs full fidelity. |
| Latency | On an M5, a fresh 8k-token state prefills in about 1.4 s and then answers off the cached prefix in ~74 ms; at 65k tokens it is ~21 s fresh / ~202 ms cached. An endpoint's payload changes every run, so that cache helps within a run's question batch, not across runs. |

Sources: the [Kev-0.8B model card](https://huggingface.co/jaredpalmer/kev-0.8b) (Kev 1.0, 2026-09-24) for serving, context and long-state measurements; [bench/results](bench/results) for the sentinel end-to-end numbers. Our own checks were run against `jaredpalmer/kev-0.8b` served locally through MLX.

## Quick start on your endpoint

```console
$ sentinel init jobs-api --url https://boards.example.com/api/jobs
wrote sentinel.yaml

questions inferred from the payload:
  currency (choice)  fail_if `choice != 'usd'`
    `currency` is USD across the baseline. A currency switch keeps every field and type intact...
  published_2026 (noul)  fail_if `p < 0.5`
    `posted_at` is a date field, mostly 2026 in the baseline. Stale content keeps the schema...
  description_content (noul)  fail_if `p < 0.5`  (verify)
    `description` is the payload's long-text field. Content replacement keeps the schema intact...
```

Then make the thresholds real and prove the layers work:

```bash
sentinel calibrate jobs-api   # clean vs injected scores → measured thresholds
sentinel check jobs-api
sentinel chaos --endpoint jobs-api
```

`sentinel init` records the baseline immediately. If the URL is not reachable at setup time, record a baseline later from a real response with `sentinel baseline set jobs-api` (or point it at a recorded file with `--fixture`). Pass `--no-suggest` to get the two generic guard questions instead of inferred ones, and `sentinel suggest jobs-api` to re-infer later without rewriting the config.

### Starting without writing questions

Question inference is heuristic, not magic. It looks for fields whose change would be semantic:

| Signal in the payload | Question it proposes |
|---|---|
| a currency code field | choice: which currency are the amounts in? |
| `posted_at`, `published_at`, `updated_at`, … | yes/no: were these published/updated in 2026? |
| a top-level or low-cardinality status field | choice: what is the overall status? |
| numeric series with a `*_units` map (`hourly.temperature_2m` + `hourly_units`) | yes/no: are the values within the observed range and stated unit? (catches C→F and scale regressions) |
| a long-text field (`description`, `summary`, …) | yes/no: do these describe the item, rather than advertising? (domain-aware wording for jobs/news content) |
| dates in metadata only (`generated_at`, `checked_at`, `date`) | yes/no: do the dates in this payload fall in 2026? |

Everything else is a `p > 0.25` error-page guard. Every suggestion is labelled high or medium confidence; medium ones are marked `(verify)` and should be confirmed with `sentinel calibrate` before they gate a deploy.

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
| `body` | unset | request body; objects/lists are sent as JSON, strings are parsed as JSON when possible |
| `stream_timeout_s` | `12` | seconds to collect from a `text/event-stream` before stopping |
| `watch` | unset | exact-value watch over selected fields: `{key, fields, tolerance, report_removed}`; see [examples/llm-prices.yaml](examples/llm-prices.yaml) |
| `questions` | required unless `watch` is set | map of question id → question; `{}` makes a deterministic, Kev-free endpoint |
| `state` | built-in template | placeholders: `{name} {url} {status} {content_type} {payload} {body} {request}` |
| `allow_extra_fields` | `true` | new fields are a warning, not a failure |
| `expected_status` | `200` | required status code |
| `expect_json` | `true` | parse and fingerprint the body as JSON |
| `min_items` | unset | minimum record count |
| `count_floor` | `0.5` | fail when records drop below this fraction of baseline |
| `timeout_s` | `15` | fetch timeout |
| `notes` | `""` | free text for humans |

Question types: `noul` (yes/no probability), `choice` (enum), `score` (ordered level). Metrics available in `fail_if`: `p` and `confidence` for choice, `score` and `confidence` for score, `p` for noul. The choice metrics are validated when the config loads.

### POST and streaming endpoints

Many useful endpoints are not GET-JSON. `sentinel init` can send a request body,
follow redirects, and parse server-sent events:

```bash
sentinel init my-agent \
  --url http://localhost:8000/chat/ \
  --method POST \
  --body '{"messages":[{"role":"user","content":"Hello, what can you do?"}]}' \
  --stream-timeout 20
```

- `--header 'authorization: Bearer ...'` adds request headers (repeatable).
- `text/event-stream` responses are parsed into `{"events": [{"event": ..., "data": ...}]}`. That object is what the schema rules fingerprint and what Kev reads, so questions like "does this stream contain a substantive assistant reply?" work on the stream itself.
- `--stream-timeout` bounds how long a stream is collected: the check stops at that deadline, at 256 KB, or at a `done`/`complete`/`[DONE]` event, whichever comes first.
- `--text` monitors a plain-text endpoint instead of JSON.

Init refuses to record a baseline from a non-2xx response: a 405 gets a hint to
retry with `--method POST`. It also refuses a stream that only sends
connection/keep-alive events, because that baseline would only catch connection
changes, not agent behaviour. Raise `--stream-timeout`, send the body a real
client sends, or pass `--allow-handshake-only` if that is genuinely the expected
response (the meaningless `stream_reply` question is dropped in that case).

`stream_reply` is a presence check: it catches an agent that stops answering
(reply ≈ 0.62 vs handshake-only ≈ 0.45 on Kev-0.8B in our check), not one that
answers with the wrong content. Add a domain question for that.

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
| `sentinel init NAME --url URL [--pack PACK] [--calibrate]` | infer questions (or use a pack), write the config, record the baseline; `--calibrate` measures thresholds |
| `sentinel import openapi SOURCE --out FILE` | generate endpoints, watches and questions from an OpenAPI 3 document |
| `sentinel pack list` / `sentinel pack show NAME` | browse and print the shipped question packs |
| `sentinel suggest NAME` | print inferred questions as a YAML block (does not edit the config) |
| `sentinel demo` | baseline, clean check, injected drift, verdict — the 10-second demo |
| `sentinel baseline set NAME...` | fetch endpoints and record new baselines |
| `sentinel baseline show [NAME]` | print one baseline in detail, or list every baseline when NAME is omitted |
| `sentinel check [NAME...]` | run rules and Kev against the baselines |
| `sentinel watch [NAME...]` | check on an interval until interrupted (long-running mode) |
| `sentinel preflight [NAME...]` | GO/NO-GO gate: baseline, clean check, margins, chaos coverage |
| `sentinel schedule` | print cron/systemd/GitHub/watch setup for this config |
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

Injectors: `wrong-entity`, `stale-dates`, `currency-swap`, `unit-swap`, `status-flip`, `stream-silence`, `scale-series` (semantic-only), plus `field-rename`, `null-fields`, `empty-list`, `string-numbers`, `status-500`, `html-error`, `truncated` (structural). An injector that does not apply to a payload (say, `unit-swap` on a job board) is reported as `n/a` and excluded from the assertion.

Use `--assert` in CI: it exits nonzero if any failure class goes undetected across the configured endpoints. A row that is applicable but undetected is a gap for that endpoint — add a question that covers it, or accept it explicitly.

## Benchmark

Measured 2026-11-23 on an Apple M1 (16 GB) with `jaredpalmer/kev-0.8b` served via MLX, Python 3.13, over the five example payloads. 50 drift cases, each run twice (100 drift runs) and 5 clean payloads, each run twice (10 clean runs).

| Backend | Detection | False positives | p50 per check | p95 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|---|
| rules only | 68/100 (68%) | 0/10 | 0.0 ms | 0.2 ms | 0.1 ms | $0 |
| Kev-0.8B + rules | 94/100 (94%) | 0/10 | 134.8 ms | 1183.4 ms | 304.1 ms | $0.0015 |

Cost is electricity only (60 W at $0.30/kWh) from the mean latency; hardware amortization is excluded. A cloud comparison is not published because a fair one needs the provider's current prices — [`bench/judge_openai.py`](bench/judge_openai.py) is a ready adapter for any OpenAI-compatible endpoint, and `bench/bench.py --judge-cmd` runs it over the same labelled set.

The 6 missed drift runs are all one class: `wrong-entity` on payloads with no long-text field (pricing, weather, status). Content replacement over short scalar fields is a known blind spot — the payload shape barely changes, and there is nothing for a content question to read. That is the intended failure mode of a question-driven tool: the gap is visible, attributable, and closable by adding a field-specific question if your endpoint has one.

### Smaller checkpoints

There is no Kev-0.4B. The smallest published checkpoint is Kev-0.5B — the
Qwen2.5-0.5B prototype, superseded and shipped at temperature 1.0 (no fitted
calibration). It was measured on the same labelled set with thresholds
recalibrated for it ([bench/configs/kev-0.5b.yaml](bench/configs/kev-0.5b.yaml)):

| Checkpoint | Detection | False positives | p50 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|
| Kev-0.8B + rules (Qwen3.5, MLX) | **94/100 (94%)** | **0/10** | **134.8 ms** | 304.1 ms | $0.0015 |
| Kev-0.5B + rules (Qwen2.5, torch/MPS) | 88/100 (88%) | 2/10 | 297.5 ms | 303.9 ms | $0.0015 |

The 0.5B is smaller on disk but worse on every axis that matters here: it misses
more drift classes, produced two false positives on the clean jobs payload (its
choice question calls USD salaries "unclear"), and is slower at p50 because
Qwen2.5 is attention-only and has no MLX path on Apple Silicon. Thresholds also
had to be loosened a lot, which is expected from an uncalibrated checkpoint.

**0.8B is the practical floor.** If you need a smaller footprint than that, lower
`sample_records` and `max_state_chars` before reaching for an older checkpoint.
The merged table lives at [bench/results/model-comparison.md](bench/results/model-comparison.md).

What the numbers mean:

- **Detection**: the rules-only row is the schema-validator baseline; the second row is the full pipeline. Kev adds 26 detections that structural checks cannot make, with no false positives on the clean payloads.
- **Latency** varies with state length and prefix-cache hits. p50 is the typical warm check; p95 covers long payloads whose state was not in the cache.
- **Cost** is dominated by hardware you already own; the electricity estimate is included to make the comparison with per-token APIs concrete.

Reproduce:

```bash
# with a Kev server running
uv run python bench/bench.py --config examples/sentinel.yaml --label kev-0.8b --repeats 2 \
  --json bench/results/kev-0.8b.json --markdown bench/results/kev-0.8b.md
```

The committed results are in [`bench/results/`](bench/results); the methodology is in [`bench/README.md`](bench/README.md).

Real-endpoint recipes (Open-Meteo, OpenRouter prices, GitHub status, Wikipedia,
GitHub metadata) are in [docs/cookbook.md](docs/cookbook.md).

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

The niche is the combination: semantic, per-endpoint, cheap enough to run hourly, and local. [docs/prior-art.md](docs/prior-art.md) records what was checked and what was not. If you know a tool that already does this, please open an issue.

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
uv run ruff check .           # lint
uv run pytest                 # 145 tests, no weights or network
make check                    # rules-only check over the example fixtures
make demo                     # requires a Kev server on :8009
make demo-offline             # the same story, rules layer only
make bench
```

Layout: `sentinel/core` (fetch, fingerprint, store, state), `sentinel/rules` (deterministic), `sentinel/sem` (Kev client and answer evaluation), `sentinel/chaos.py` (injectors), `sentinel/calibrate.py`, `sentinel/cli.py`. Tests use a fake judge, so the suite runs on any machine.

## Attribution and license

kev-sentinel is independent software, Apache-2.0. It uses the Kev model family, serving code, and API design from [jaredpalmer/kev](https://github.com/jaredpalmer/kev) (Apache-2.0), which builds on the architecture described in [Jev's Architecture Unmasked](https://archerhume.com/posts/jevs-architecture-unmasked). It is not affiliated with, endorsed by, or maintained by the Kev authors or Vercel. See [NOTICE](NOTICE).