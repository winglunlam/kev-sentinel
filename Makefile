.PHONY: install lint test check baseline kev demo demo-model demo-offline bench clean

KEV_DIR ?= .kev
KEV_PORT ?= 8009
KEV_RUN ?= jaredpalmer/kev-0.8b

install:
	uv sync --extra dev

# Clone (once), install, and serve Kev-0.8B on KEV_PORT (default 8009).
# The first run downloads the base model and adapter.
kev:
	@if [ ! -d "$(KEV_DIR)" ]; then \
		echo "cloning Kev into $(KEV_DIR) (first run only)"; \
		git clone --depth 1 https://github.com/jaredpalmer/kev "$(KEV_DIR)"; \
	fi
	cd "$(KEV_DIR)" && uv sync --extra serve
	cd "$(KEV_DIR)" && uv run --extra serve python -m kev.serve --run "$(KEV_RUN)" --port "$(KEV_PORT)"

lint:
	uv run ruff check .

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

# Requires a Kev server (`make kev` in another terminal).
demo-model:
	uv run sentinel demo --config examples/agent-quality.yaml --inject refusal

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
