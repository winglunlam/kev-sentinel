# Prior art

Checked 2026-11-22 against the projects' own READMEs and documentation. This
page exists because "semantic drift monitoring for endpoints" is a crowded-
sounding phrase; it is worth being precise about what already exists and what
this tool does differently.

## What is already solved

| Tool / category | What it checks | Where it runs | Model-based meaning? |
|---|---|---|---|
| [Soda Core](https://github.com/sodadata/soda-core) | data contracts: schema and data checks (row counts, missing, invalid, distributions) | warehouses and data sources, locally or in pipelines | no |
| [Great Expectations](https://github.com/great-expectations/great_expectations) | "Expectations": unit tests for data, validated in pipelines | data pipelines / warehouses | no |
| [dbt tests](https://docs.getdbt.com/docs/build/data-tests) | SQL assertions on transformed tables | transformation layer | no |
| [JSON Schema](https://json-schema.org) / OpenAPI validators | response shape, required fields, types | any HTTP boundary | no |
| uptime / API monitors | status code, latency, sometimes a JSON path assertion | external probe | no |
| synthetic monitoring | scripted browser or HTTP flows | external probe | no |
| hosted LLM evaluation / "LLM-as-judge" services | open-ended text quality, semantic similarity | cloud API | yes |

The first six are structural. They answer "is it up?", "is the shape right?",
and "is this table plausible after ingestion?" They cannot tell you that a status
enum flipped, that a currency changed, that a forecast's values were silently
converted to Fahrenheit, or that a job board started returning advertising text
inside the same fields.

The last category can see meaning, but it is a cloud call per judgment. Running
it for every endpoint every hour is a real bill and often sends the payload to a
third party.

## What this project adds

- **Per-endpoint semantic checks, cheap enough to run every hour.** A local Kev
  checkpoint answers a small set of endpoint-specific questions in one batched
  prefill. No text generation, no egress, probabilities calibrated at the
  checkpoint.
- **Two layers with explicit ownership.** Structural fingerprint rules catch what
  schema validation catches, and say so. Kev covers what structure cannot. The
  `chaos` command tells you which layer caught which injected change instead of
  claiming that "AI" caught everything.
- **Thresholds as reviewable config.** Every question declares `fail_if`, and
  `calibrate` measures the clean and drifted scores that justify it.
- **The baseline is a payload, not a table.** This is aimed at API responses and
  scraped payloads before ingestion, where warehouse tools do not run.

## What was not checked

- Hosted data-observability vendors (Monte Carlo, Anomalo, Sifflet, WhyLabs and
  similar). These are mostly warehouse/data-pipeline quality and anomaly
  detection; verify their current API-monitoring features before assuming a gap.
- Commercial API contract testing suites; most are structural.
- Semantic-search or embedding-drift tooling; different failure model.

If you know a tool that already does local, per-endpoint, model-based drift
checks with calibrated thresholds, open an issue and it will be added here.
