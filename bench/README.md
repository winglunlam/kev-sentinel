# Benchmark methodology

Every benchmark number in the README comes from `bench/bench.py`. The harness is
deliberately boring: it builds a labelled set out of the example fixtures, runs
each backend over the same set, and reports detection, false positives, latency,
and an electricity cost estimate.

## Labelled set

For each example endpoint:

- **Clean** — the recorded fixture, labelled negative.
- **Drifted** — every chaos injection that actually changes the payload,
  labelled positive. An injection that leaves the JSON identical is skipped
  (`n/a` in `sentinel chaos`, excluded here too).

The set is rebuilt from scratch on every run, so it cannot go stale. It is small
(46 drift cases, 5 clean payloads) and that is intentional: it is a regression
harness for the example config, not a product benchmark.

## Backends

| Backend | What it runs |
|---|---|
| `rules only` | the deterministic layer (schema fingerprint, status, counts, size, blank ratio) |
| `kev + rules` | the same rules plus every semantic question, one batched Kev request per case |

Both are measured against the same baseline fingerprints. Detection means "any
rule blocked or any `fail_if` condition failed".

The optional `--judge-cmd` backend sends one JSON object to an external command:

```json
{"state": "...", "questions": {"qid": {"type": "noul", "instructions": "..."}}}
```

and expects the System One answer shape back:

```json
{"answers": {"qid": {"type": "noul", "noul": 0.12}}}
```

A minimal adapter:

```python
import json, sys
payload = json.load(sys.stdin)
answers = {}
for qid, question in payload["questions"].items():
    # call your provider here; this is the only part you need to write
    answers[qid] = {"type": "noul", "noul": ask_your_model(payload["state"], question)}
json.dump({"answers": answers}, sys.stdout)
```

Then run:

```bash
uv run python bench/bench.py --config examples/sentinel.yaml \
  --judge-cmd ./bench/my-judge.py --repeats 2
```

The template only shows the `noul` shape; `choice` and `score` answers use
`{"type": "choice", "choice": "...", "confidence": 0.0, "probabilities": {...}}`
and `{"type": "score", "score": 0.0, "confidence": 0.0}`. The parser is in
`sentinel/sem/client.py`.

## What is measured

- **Latency** is wall time for the rules pass plus one batched Kev request,
  excluding the HTTP fetch of the payload and the first warm-up request. Latency
  includes the state-building step. Each case runs `--repeats` times; p50, p95
  and mean are over all runs.
- **Cost** is electricity: `mean_seconds × watts × 1000 / 3,600,000 × $/kWh`.
  Hardware amortization and idle power are excluded. The defaults (60 W,
  $0.30/kWh) are assumptions — change them with `--watts` and
  `--price-per-kwh` and say so when you compare.
- **False positives** are clean payloads flagged as drifted. A checker that
  cries wolf is worse than no checker, so this column matters more than
  detection.

## Reproducing

```bash
# terminal 1
cd ../kev-main && uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009

# terminal 2
uv run python bench/bench.py --config examples/sentinel.yaml --repeats 2 \
  --json bench/results/kev-0.8b.json --markdown bench/results/kev-0.8b.md
```

Results in this directory were produced on an Apple M1 (16 GB), macOS 26.4.1,
Python 3.13.14, Kev-0.8B served through MLX (`backend: mlx`, temperature 2.35).
