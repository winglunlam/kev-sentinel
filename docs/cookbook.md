# Cookbook: real public endpoints

Five runnable recipes. Every command works against a real public API; the
examples in the repository use recorded fixtures so they also run offline. Before
scheduling anything, run `sentinel preflight` and see
[the production checklist](#production-checklist).

## 1. Forecast units: Open-Meteo

The classic silent failure: Celsius values relabelled as Fahrenheit, or a scale
regression, while the keys and `*_units` map stay valid.

```bash
sentinel init weather \
  --url "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current=temperature_2m,wind_speed_10m&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m"
sentinel calibrate weather
sentinel check weather
sentinel chaos --endpoint weather --inject unit-swap --inject scale-series
```

Inferred questions: `temperature_2m_plausible`, `relative_humidity_2m_plausible`,
`wind_speed_10m_plausible`, `dates_2026`. Catches `°C`→`°F` values, scale
regressions, and stale dates. Offline version: `examples/open-meteo.yaml`.

## 2. LLM price changes: OpenRouter

Exact values, so no model: a `watch` block compares per-token prices by model id.

```bash
sentinel baseline set --config examples/llm-prices.yaml openrouter-models --live
sentinel check --config examples/llm-prices.yaml --live
```

Fails the moment any watched `pricing.prompt` or `pricing.completion` changes.
Change one price and re-run to see the diff. Cron it hourly; see
[examples/llm-prices.yaml](../examples/llm-prices.yaml).

## 3. Service status: GitHub Status API

```bash
sentinel init github-status \
  --url https://www.githubstatus.com/api/v2/status.json \
  --pack status \
  --header 'user-agent: kev-sentinel'
sentinel calibrate github-status
sentinel check github-status
```

The `status` pack asks whether services are operational and whether an outage is
reported. The payload is small (`page`, `status.indicator`), so `calibrate` will
tell you whether the generic question separates clean from injected drift; if the
margin is weak, replace it with a question about the exact wording:

```yaml
status_ok:
  type: noul
  instructions: Does the status page report all systems operational?
  fail_if: "p < 0.6"
```

## 4. Article content: Wikipedia summary API

```bash
sentinel init wiki-summary \
  --url "https://en.wikipedia.org/api/rest_v1/page/summary/Ada_Lovelace" \
  --pack documents \
  --header 'user-agent: kev-sentinel'
sentinel calibrate wiki-summary
sentinel check wiki-summary
```

The `documents` pack checks that a real document body is present and substantive,
rather than a stub, a redirect notice, or boilerplate. Useful for any feed of
articles, filings, or reports.

## 5. Repository metadata: GitHub API

Hand-write two questions here; the payload is a single object, which is where
payload inference is weakest and `--pack`/`--llm-draft` are better:

```bash
sentinel init github-repo \
  --url https://api.github.com/repos/jaredpalmer/kev \
  --header 'user-agent: kev-sentinel'
```

Edit `sentinel.yaml`:

```yaml
questions:
  repo_present:
    type: noul
    instructions: >
      Does this payload describe a repository with a name, owner, and default
      branch, rather than an error object or a different resource?
    fail_if: "p < 0.6"
  archived:
    type: noul
    instructions: Is the repository archived or disabled?
    fail_if: "p > 0.2"
```

This catches an API returning an error body with HTTP 200, or the repository being
archived without a schema change. Or draft questions with an LLM:

```bash
SENTINEL_DRAFT_API_KEY=... sentinel init github-repo \
  --url https://api.github.com/repos/jaredpalmer/kev --llm-draft
```

Then the usual `calibrate`, `chaos`, `preflight`.

## Production checklist

1. `sentinel init --calibrate` (or import/pack) → a draft config.
2. `sentinel chaos --endpoint NAME` → prove the injectors are caught.
3. `sentinel preflight --config sentinel.yaml` → GO before scheduling.
4. `sentinel schedule --config sentinel.yaml --every 1h --lock` → cron/systemd/GitHub.
5. Export `SENTINEL_WEBHOOK_URL` for transition alerts.
6. Re-baseline deliberately after intentional changes; `baseline_max_age_days`
   (default 30) makes preflight flag stale baselines.
