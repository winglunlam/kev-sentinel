# Changelog

## 0.2.0 — 2026-11-23

The public-use release: question generation from specs and packs, a readiness
gate, and unattended operation.

- **OpenAPI import** — `sentinel import openapi SPEC --out config` turns
  documented APIs into endpoints, watches and questions. Example responses are
  used when present; schema-only operations get date/range/currency questions.
  `--fetch` calls endpoints and infers from live responses; path parameters,
  POST body examples, auth headers, `--limit` and skipped-operation notes are
  handled.
- **Question packs** — `sentinel pack list/show`, `sentinel init --pack NAME`.
  Packs are YAML in `sentinel/packs/` (agent, listings, news, pricing, status,
  documents, weather) with a `{year}` placeholder; adding a domain needs no
  Python.
- **Optional LLM drafting** — `sentinel init --llm-draft` asks an
  OpenAI-compatible model for questions; every question is validated by the same
  parser and still has to pass calibration and preflight.
- **`sentinel preflight`** — GO/NO-GO gate over baseline, clean check, question
  margins, chaos coverage and stale baselines. Exit codes suitable for CI.
- **Unattended operation** — `sentinel watch` (long-running), `sentinel schedule`
  (cron/systemd/GitHub/watch output), `--lock` overlap protection, transition
  alerts with a cooldown.
- **More accurate inference** — unit-annotated series, nested content replacement,
  POST/SSE payloads, event-stream questions, and heuristic hardening (no more
  mistaking `stargazers_count` for a temperature).
- Benchmark refreshed: rules 68/100, Kev + rules 94/100, 0/10 false positives,
  p50 134.8 ms, ~$0.0015 per 1,000 checks on Kev-0.8B.

## 0.1.0 — initial scaffold

- Two-layer checks (structural rules + calibrated Kev questions), SQLite
  baselines/history, chaos injectors, calibration, CLI, SQLite store, webhook
  alerts, examples, tests and benchmark harness.
