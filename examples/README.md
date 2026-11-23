# Examples

Five endpoints, recorded as fixtures so the whole config runs offline. The
payloads are synthetic but shaped like real API responses; company and product
names are fictional.

| Endpoint | Shape | Failure the questions catch |
|---|---|---|
| `jobs-api` | job board search | descriptions replaced by advertising, stale posting dates, currency switch |
| `pricing-api` | storefront price feed | prices switch from USD to EUR while the schema stays valid |
| `weather-forecast` | multi-day forecast | Fahrenheit values under `"units": "metric"` |
| `news-feed` | article list | articles go from 2026 to 2019 |
| `status-page` | service status | `ok` flips to `outage`, `operational` to `down` |
| `open-meteo` | forecast with parallel arrays + `*_units` (recorded Open-Meteo response) | Celsius values relabelled as Fahrenheit, or a numeric scale regression |
| `llm-prices` | OpenRouter model catalogue (recorded, 24 models) | any per-token price change, compared exactly by model id |

## One-command demo

```bash
sentinel demo --config examples/sentinel.yaml          # needs Kev
sentinel demo --config examples/sentinel.yaml --no-sem # rules layer only
```

It records missing baselines, checks every endpoint, injects a silent change, and
prints which layer caught it.

## Run it without Kev (rules only)

```bash
sentinel baseline set --config examples/sentinel.yaml \
  jobs-api pricing-api weather-forecast news-feed status-page
sentinel check --config examples/sentinel.yaml --no-sem
sentinel chaos --config examples/sentinel.yaml --no-sem --assert
```

## Run it with Kev

```bash
# in a Kev checkout
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009

# here
sentinel check --config examples/sentinel.yaml
sentinel chaos --config examples/sentinel.yaml
sentinel calibrate --config examples/sentinel.yaml
```

If your server is elsewhere, export `SENTINEL_KEV_BASE_URL=http://host:port`.

## OpenRouter LLM prices (exact watch, no Kev needed)

`examples/llm-prices.yaml` watches `pricing.prompt` and `pricing.completion` for
every model by `id`. It is the reference for deterministic drift: prices are
exact values, so a watch block is more reliable and cheaper than a semantic
question. The config runs offline against a recorded response; add `--live` to
hit `api.openrouter.ai`.

```bash
sentinel baseline set --config examples/llm-prices.yaml openrouter-models
sentinel check --config examples/llm-prices.yaml
sentinel chaos --config examples/llm-prices.yaml --inject price-change
```

The recorded fixture is a trimmed 24-model response; record a live baseline with
`--live` for real monitoring.

## Open-Meteo (real public API shape)

`examples/open-meteo.yaml` points at a recorded response from
`api.open-meteo.com`, so it runs offline:

```bash
sentinel baseline set --config examples/open-meteo.yaml weather
sentinel check --config examples/open-meteo.yaml
sentinel chaos --config examples/open-meteo.yaml --endpoint weather --inject unit-swap
```

It is the reference for unit-annotated series: `hourly.temperature_2m` plus
`hourly_units.temperature_2m` become a range question ("between -5 and 5 degrees
Celsius"). To use the live API, replace `fixture:` with the `url:` from the
header comment and drop `--live` handling as needed.

## Baselines

`.sentinel/sentinel.db` is created next to `sentinel.yaml` and gitignored. Record
baselines once per checkout; if you change a fixture on purpose, re-run
`sentinel baseline set ...` and review the schema diff in the PR.

## Thresholds

The `fail_if` values in `sentinel.yaml` were calibrated against Kev-0.8B
(2026-09-21 checkpoint) on these payloads. They are not universal: re-run
`sentinel calibrate` against your checkpoint and keep the margins you are
comfortable with.
