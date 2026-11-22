# Contributing

## Development

```bash
uv sync --extra dev
uv run pytest
```

The test suite uses a fake judge, so it needs no weights, no network, and no GPU.
If you change the Kev client contract, add a test with `httpx.MockTransport` that
asserts the request body and the answer parsing.

Before opening a pull request:

```bash
make check          # rules-only check over the examples, offline
uv run python -m compileall -q sentinel
```

`make demo` and `make bench` need a Kev server on `http://127.0.0.1:8009`.

## Adding a question

Questions live in `sentinel.yaml`, not in code. When you add one:

1. State the failure it detects in the pull request, with a payload that shows
   the difference.
2. Run `sentinel calibrate` and include the clean score, the worst injected
   score, and the margin. A margin under 0.10 (`noul`) or 0.5 (`score`) should be
   treated as no signal.
3. Run `sentinel chaos` and confirm the class you care about is caught by the
   layer you expect.

Questions that are always 0.02 or always 0.98 are not measurements; prefer a
different phrasing or a different signal.

## Adding a chaos injector

`sentinel/chaos.py` has one function per injector. An injector must:

- be a deterministic rewrite of a real payload (no random values),
- keep the payload parseable unless it is explicitly simulating a broken body,
- declare which layer should catch it (`expected`), and
- have a test in `tests/test_chaos.py`.

## Style

- Python 3.11+, standard library where possible, no async for one-request-per-check.
- Keep the deterministic rules free of model assumptions and keep the semantic
  layer free of HTTP-specific logic.
- User-facing strings use plain technical English. Numbers carry their
  provenance (checkpoint, payload, run) or they are not written down.

## Attribution

The Kev model and serving code are Apache-2.0 and belong to the Kev project. Do
not describe this project as affiliated with or endorsed by the Kev authors.
