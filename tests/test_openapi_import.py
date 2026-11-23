"""OpenAPI import: schema/example-driven endpoints, watches and questions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
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


class _LiveApi(BaseHTTPRequestHandler):
    paths: list[str] = []
    headers_seen: list[dict] = []

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _LiveApi.paths.append(self.path)
        _LiveApi.headers_seen.append(dict(self.headers))
        if self.path == "/prices":
            self._send(200, {"currency": "EUR", "products": [{"id": "a", "price": 10}, {"id": "b", "price": 12}]})
        elif self.path == "/weather/berlin":
            self._send(200, {"city": "Berlin", "generated_at": "2026-11-23T10:00:00Z", "temperature_c": 5})
        else:
            self._send(500, {"error": "down"})

    def log_message(self, *args):
        pass


@pytest.fixture
def live_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LiveApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_import_fetch_uses_live_responses(tmp_path, live_api, capsys):
    _LiveApi.paths.clear()
    out = tmp_path / "sentinel.yaml"
    assert cli.main(
        [
            "import",
            "openapi",
            str(FIXTURE),
            "--base-url",
            live_api,
            "--fetch",
            "--api-header",
            "x-test: yes",
            "--out",
            str(out),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "fetched HTTP 200" in output
    assert "HTTP 500" in output
    assert all(any(key.lower() == "x-test" for key in headers) for headers in _LiveApi.headers_seen)

    config = load_config(out)
    prices = config.endpoint("list-prices")
    assert "currency" in prices.questions
    assert prices.questions["currency"].fail_if.raw == "choice != 'eur'"
    assert prices.watch is not None and prices.watch.key == "id"
    weather = config.endpoint("get-weather")
    year = datetime.now(timezone.utc).year
    assert f"dates_{year}" in weather.questions
    # the failing endpoint keeps its schema-derived fallback questions
    assert "error_page" in config.endpoint("health").questions
