# Why not just call Kev directly?

Kev is the judgement engine. It answers questions about **one document at a
time** and returns probabilities. It does not know what the document looked like
yesterday, which fields must stay byte-identical, which questions are worth
asking, what probability should page someone, or whether the check still works.
Those are the parts kev-sentinel supplies.

| Capability | Kev API alone | Hand-rolled script around Kev | kev-sentinel |
|---|---|---|---|
| Compare against a recorded baseline | no | you build it | schema fingerprint + exact watches stored in SQLite |
| Catch structural drift without the model | no | separate code, usually ad hoc | rules layer: schema, counts, null/blank/size, HTML/JSON, series lengths |
| Decide what to ask | no | you write prompts per endpoint | inferred from the payload, with rationale, edited in YAML |
| Turn probabilities into decisions | no | thresholds hard-coded | `fail_if` in reviewable config, measured by `calibrate` |
| Prove the checks catch drift | no | you write fixtures by hand | `chaos` injectors + `--assert`, runnable in CI |
| Alert on transitions, not every run | no | you build dedupe/cooldown | webhook, pass→fail transition, cooldown |
| History and reporting | no | you add a log/db | snapshot + run store, `report`, JSON output |
| Cron/CI ergonomics | no | glue per project | exit codes, `--github-summary`, composite action |
| Refuse a bad baseline | no | easy to miss | rejects non-2xx, HTML, handshake-only streams, missing JSON |
| Handle POST bodies and SSE | no | per-project plumbing | `method`, `body`, headers, stream parsing |
| Model/checkpoint portability | one endpoint at a time | rewrite scripts | change `sem.model`, re-run `calibrate`, compare with `bench` |

## A script around Kev is fine when…

- you monitor one endpoint with one question,
- you already trust the threshold without measuring it,
- a failed check means "look at the logs", not "page someone",
- nothing downstream depends on the check being correct.

## The repo earns its keep when…

- you monitor several endpoints and want one reviewed config instead of N scripts,
- a false positive costs trust, so thresholds must be measured (`calibrate`) and the
  coverage must be demonstrable (`chaos --assert`),
- the check runs on a schedule or in CI and has to alert, exit, and keep history,
- you need exact checks (prices, enums, counts) and fuzzy checks (is this still a
  listing? did the agent answer?) in the same system,
- you switch Kev checkpoints and need to re-measure rather than guess.

## The design principle

**Rules where you can specify the failure; Kev where you can only describe it.**
The repo's job is to keep that boundary honest: a price belongs in `watch`, an
answer quality belongs in a question, and `chaos` proves which layer actually
catches the thing you fear.

That boundary is also the product. `examples/llm-prices.yaml` needs no model at
all; `examples/agent-quality.yaml` cannot work without one. Shipping both, with
measured numbers, is more useful than claiming the model is necessary for
everything.
