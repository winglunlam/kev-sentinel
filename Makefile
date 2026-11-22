.PHONY: install test check baseline demo demo-offline bench clean

install:
	uv sync --extra dev

test:
	uv run pytest

baseline:
	uv run sentinel baseline set --config examples/sentinel.yaml \
		jobs-api pricing-api weather-forecast news-feed status-page

check: baseline
	uv run sentinel check --config examples/sentinel.yaml --no-sem

# No Kev server required: rules layer only.
demo-offline: baseline
	uv run sentinel demo --config examples/sentinel.yaml --no-sem

# Requires a Kev server, e.g.
#   cd ../kev-main && uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009
demo: baseline
	uv run sentinel check --config examples/sentinel.yaml
	uv run sentinel chaos --config examples/sentinel.yaml
	uv run sentinel calibrate --config examples/sentinel.yaml

bench:
	uv run python bench/bench.py --config examples/sentinel.yaml \
		--json bench/results/local.json --markdown bench/results/local.md

clean:
	rm -rf examples/.sentinel .sentinel bench/results
