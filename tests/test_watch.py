"""Exact-value watches: discovery, comparison, and baseline storage."""

from __future__ import annotations

import json

import pytest

from sentinel.config import ConfigError, EndpointConfig, WatchConfig, load_config
from sentinel.core.fetch import synthetic_result
from sentinel.watch import compare_watch, discover_watch_targets, fingerprint_with_watch

from .conftest import write_config

PAYLOAD = {
    "data": [
        {"id": "openai/gpt-x", "pricing": {"prompt": "0.000002", "completion": "0.000008"}},
        {"id": "google/gemini", "pricing": {"prompt": "0.000003", "completion": "0.000012"}},
    ]
}


def _endpoint(fields=("pricing.prompt", "pricing.completion"), key="id", tolerance=0.0, report_removed=False):
    return EndpointConfig(
        name="prices",
        questions={},
        watch=WatchConfig(
            fields=fields, key=key, tolerance=tolerance, report_removed=report_removed
        ),
    )


def test_discover_keyed_values():
    values = discover_watch_targets(_endpoint(), PAYLOAD)
    assert values["openai/gpt-x pricing.prompt"] == "0.000002"
    assert values["google/gemini pricing.completion"] == "0.000012"


def test_discover_root_values_without_key():
    endpoint = EndpointConfig(
        name="root", questions={}, watch=WatchConfig(fields=("data.0.pricing.prompt",))
    )
    values = discover_watch_targets(endpoint, PAYLOAD)
    assert values == {}  # list indexes are not dotted paths; no match is fine


def test_compare_exact_and_relative_tolerance():
    baseline = {"a": "0.000002", "b": "0.000010"}
    current = {"a": "0.0000024", "b": "0.0000105"}
    exact = compare_watch(baseline, current, WatchConfig(fields=("x",)))
    assert len(exact) == 2
    tolerant = compare_watch(baseline, current, WatchConfig(fields=("x",), tolerance=0.2))
    assert tolerant == []


def test_compare_reports_removed_only_when_asked():
    baseline = {"a": "1", "gone": "2"}
    current = {"a": "1"}
    assert compare_watch(baseline, current, WatchConfig(fields=("x",))) == []
    removed = compare_watch(
        baseline, current, WatchConfig(fields=("x",), report_removed=True)
    )
    assert removed and removed[0].kind == "removed"


def test_fingerprint_with_watch_records_values():
    result = synthetic_result("prices", body=json.dumps(PAYLOAD).encode())
    fingerprint = fingerprint_with_watch(_endpoint(), result)
    assert fingerprint["watch"]["openai/gpt-x pricing.prompt"] == "0.000002"
    assert fingerprint["item_count"] == 2


def test_config_parses_watch_and_allows_empty_questions(tmp_path):
    path = write_config(
        tmp_path,
        """\
version: 1
endpoints:
  - name: prices
    fixture: f.json
    watch:
      key: id
      fields: [pricing.prompt]
      tolerance: 0.01
      report_removed: true
    questions: {}
""",
    )
    (tmp_path / "f.json").write_text("{}")
    endpoint = load_config(path).endpoint("prices")
    assert endpoint.watch is not None
    assert endpoint.watch.key == "id"
    assert endpoint.watch.tolerance == 0.01
    assert endpoint.watch.report_removed is True
    assert endpoint.questions == {}


def test_config_rejects_empty_questions_without_watch(tmp_path):
    path = write_config(
        tmp_path,
        """\
version: 1
endpoints:
  - name: nothing
    fixture: f.json
    questions: {}
""",
    )
    (tmp_path / "f.json").write_text("{}")
    with pytest.raises(ConfigError, match="at least one question"):
        load_config(path)


def test_config_rejects_invalid_watch(tmp_path):
    path = write_config(
        tmp_path,
        """\
version: 1
endpoints:
  - name: prices
    fixture: f.json
    watch:
      fields: []
      tolerance: -1
    questions: {}
""",
    )
    (tmp_path / "f.json").write_text("{}")
    with pytest.raises(ConfigError):
        load_config(path)
