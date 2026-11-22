## Problem

What breaks today, for whom, and how you noticed.

## Change

The mechanism, one concrete artifact per idea (a config, a payload, a table, a
test). If this changes a question or threshold, include the clean score, the
drifted score, and the margin from `sentinel calibrate`.

## Verification

- [ ] `uv run pytest`
- [ ] `make check` (offline rules layer)
- [ ] `sentinel calibrate` / `sentinel chaos` output for any question change
- [ ] no secrets or payload dumps added to the repo
