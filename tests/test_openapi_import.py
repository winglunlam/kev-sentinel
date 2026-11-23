"""OpenAPI import: schema/example-driven endpoints, watches and questions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

import sentinel.cli as cli
from sentinel.config import load_config
from sentinel.openapi import endpoints_from_spec, load_spec

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "openapi-catalogue.json"


def test_endpoints_from_spec_uses_examples_and_schema():
    spec = load_spec(FIXTURE)
    endpoints, skipped = endpoints_from_spec(spec)
    by_name = {endpoint.name: endpoint for endpoint in endpoints}
    assert set(by_name) == {"list-prices", "get-weather", "health"}
    assert any("no JSON request body example" in warning for warning in skipped)

    prices = by_name["list-prices"]
    assert prices.url == "https://api.example.com/prices"
    assert prices.watch is not None
    assert prices.watch["key"] == "id"
    assert set(prices.watch["fields"]) == {"price", "compare_at_price"}
    assert "currency" in prices.questions

    weather = by_name["get-weather"]
    year = datetime.now(timezone.utc).year
    assert weather.url == "https://api.example.com/weather/berlin"
    assert f"dates_{year}" in weather.questions
    assert "temperature_c_range" in weather.questions
    assert "-30" in weather.questions["temperature_c_range"]["instructions"]

    assert "overall_status" in by_name["health"].questions


def test_import_writes_a_loadable_config(tmp_path, capsys):
    out = tmp_path / "sentinel.yaml"
    assert cli.main(["import", "openapi", str(FIXTURE), "--out", str(out)]) == 0
    output = capsys.readouterr().out
    assert "list-prices" in output and "skipped" in output
    config = load_config(out)
    assert [endpoint.name for endpoint in config.endpoints] == ["list-prices", "get-weather", "health"]


def test_import_stdout_is_valid_yaml(capsys):
    assert cli.main(["import", "openapi", str(FIXTURE)]) == 0
    document = yaml.safe_load(capsys.readouterr().out)
    assert [endpoint["name"] for endpoint in document["endpoints"]] == [
        "list-prices",
        "get-weather",
        "health",
    ]


def test_import_refuses_overwrite_without_force(tmp_path):
    out = tmp_path / "sentinel.yaml"
    out.write_text("version: 1\nendpoints: []\n")
    assert cli.main(["import", "openapi", str(FIXTURE), "--out", str(out)]) == 2
    assert cli.main(["import", "openapi", str(FIXTURE), "--out", str(out), "--force"]) == 0
    assert "list-prices" in out.read_text()


def test_import_limit():
    spec = load_spec(FIXTURE)
    endpoints, _ = endpoints_from_spec(spec, limit=1)
    assert len(endpoints) == 1
