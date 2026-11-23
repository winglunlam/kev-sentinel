# Writing questions that Kev answers well

A sentinel check is only as good as its questions. Kev is a small calibrated
decision model, not a chatbot: it reads one state (your payload) and answers many
independent questions with probabilities. Questions that fit that shape work;
open-ended ones do not.

## The shape of a good question

1. **One judgement per question.** "Does this look like a job list?" not "Is this
   a job list, and are the salaries reasonable?"
2. **About the payload in front of it.** Do not ask questions that need the
   current date, live market values, or world knowledge. Ask what is visible in
   the text. `Were these postings published in 2026?` works because the dates and
   `generated_at` are both in the state. `Is this a good time to buy?` does not.
3. **As specific as the failure you care about.** `Do the job descriptions
   mention duties or responsibilities?` catches content replaced by advertising;
   `Does this look like a jobs API?` often does not, because the keys still say
   `title` and `company`.
4. **Declare the failure condition, not a pass threshold.** `fail_if: "p < 0.55"`
   reads as "fail when Kev thinks the chance is below 55%". `fail_if: "p > 0.15"`
   is the right shape for questions where the expected answer is *no*.
5. **Use `choice` for enums and `score` for ordered plausibility.** A choice
   question catches a currency or status switch cleanly:
   `fail_if: "choice != 'usd'"`. A score question is useful when "how plausible"
   is the judgement: `fail_if: "score < 1.5"`.

## Three sources of questions

1. `sentinel import openapi SPEC --out sentinel.yaml` — documented APIs; the best
   starting point, using example responses and schema formats.
2. `sentinel init ... --pack agent|listings|news` — common endpoint archetypes,
   shipped as YAML in `sentinel/packs/` and open to community contributions.
3. `sentinel init` payload inference — the fallback for undocumented APIs.

All three are drafts until `sentinel calibrate` measures thresholds and
`sentinel preflight` returns GO.

## Start from `sentinel init`

`sentinel init` inspects the baseline payload and proposes questions for the
fields it recognises (currency, publication dates, status, temperatures, long
text). Read the proposed questions before trusting them:

```bash
sentinel init jobs-api --url https://example.com/jobs   # prints what it inferred and why
sentinel suggest jobs-api                               # re-infer later as a YAML block
```

A proposal is a hypothesis. `sentinel calibrate` is what turns it into a
measurement, and medium-confidence proposals (marked `(verify)`) should not gate
anything until the margin is acceptable.

## Let the tool draft the questions

`sentinel init --calibrate` infers the generic question set from the payload,
records the baseline, then measures each question against the semantic chaos
injections and writes the thresholds. Start there; only questions about
domain-specific judgement ("does the reply answer this customer?") need a human
to write them. `sentinel suggest NAME` re-runs inference without editing.

## Calibration loop

```bash
# 1. Record what clean looks like
sentinel baseline set jobs-api

# 2. Measure clean vs injected drift, and get a suggested threshold
sentinel calibrate jobs-api

# 3. Edit sentinel.yaml with the suggestion (and your judgement), then prove it
sentinel check jobs-api
sentinel chaos --endpoint jobs-api
```

`calibrate` runs the questions on the clean payload and on the semantic chaos
injections, then suggests the midpoint between the clean score and the worst
injected score. A margin under 0.10 (noul) or 0.5 (score) is printed as
`no separation`: that question is not measuring what you hoped, so rewrite it or
drop it.

Thresholds are checkpoint-specific. Kev-0.8B, Kev-4B and Kev-9B have different
calibration temperatures, so re-run `calibrate` when you change `--run`.

## Streaming endpoints

When the response is a `text/event-stream`, the state is the parsed event list.
The inferred question checks presence ("did the agent reply at all?"). For a chat
agent you usually want one content question too, for example:

```yaml
answer_on_topic:
  type: noul
  instructions: >
    Does the assistant reply answer the user's question about building a bot,
    rather than declining, changing the subject, or returning an error?
  fail_if: "p < 0.6"
```

`sentinel calibrate` cannot invent that judgement for you, but it will tell you
whether the question separates clean replies from the `stream-silence` injection.

Before scheduling, run `sentinel preflight`: it fails when a question is weak
(margin under 0.15), has no matching injection, or the chaos classes are not
caught. That is the gate between "drafted" and "trusted".

## Examples

Good:

```yaml
job_duties:
  type: noul
  instructions: >
    Do the job descriptions mention duties, skills, or responsibilities that a
    person would perform in the role?
  fail_if: "p < 0.55"
```

Weak — the schema still contains the words `title` and `company`, so a payload
full of advertising can still pass:

```yaml
has_titles:
  type: noul
  instructions: Does this payload have job titles?
  fail_if: "p < 0.85"
```

Good — a choice question for an enum that must not change:

```yaml
salary_currency:
  type: choice
  instructions: Are the listed salaries in US dollars, another currency, or unclear?
  criteria:
    usd: US dollars
    other: Another currency
    unclear: Missing, mixed, or unclear
  fail_if: "choice != 'usd'"
```

## Questions cost almost nothing, but they are not free

All questions for an endpoint go in one Kev request, so the marginal cost of a
third question is a few output tokens and a little compute. Keep question sets
under roughly ten per endpoint; past that the request grows and each judgement
has less context budget. `sentinel check --verbose` prints every observed value,
which is the fastest way to spot a question that is always 0.02 or always 0.98.
