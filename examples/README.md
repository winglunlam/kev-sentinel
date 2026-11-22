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

## Baselines

`.sentinel/sentinel.db` is created next to `sentinel.yaml` and gitignored. Record
baselines once per checkout; if you change a fixture on purpose, re-run
`sentinel baseline set ...` and review the schema diff in the PR.

## Thresholds

The `fail_if` values in `sentinel.yaml` were calibrated against Kev-0.8B
(2026-09-21 checkpoint) on these payloads. They are not universal: re-run
`sentinel calibrate` against your checkpoint and keep the margins you are
comfortable with.
