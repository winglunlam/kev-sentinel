from __future__ import annotations

import json

from sentinel.core.fetch import synthetic_result
from sentinel.core.fingerprint import fingerprint_result
from sentinel.core.store import Store
from sentinel.rules.deterministic import blocking_findings, looks_like_html, run_rules


def _baseline(config, store: Store, name: str):
    endpoint = config.endpoint(name)
    data = json.loads(endpoint.fixture.read_text(encoding="utf-8"))
    result = synthetic_result(name, body=json.dumps(data).encode())
    snapshot = store.record_snapshot(name, result, fingerprint_result(result))
    store.set_baseline(name, snapshot.id)
    return endpoint, store.baseline(name)


def test_clean_payload_passes(config):
    store = Store.for_home(config.home)
    try:
        endpoint, baseline = _baseline(config, store, "jobs-api")
        data = json.loads(endpoint.fixture.read_text())
        result = synthetic_result("jobs-api", body=json.dumps(data).encode())
        findings = run_rules(endpoint, result, baseline)
        assert not blocking_findings(findings)
    finally:
        store.close()


def test_removed_field_is_caught(config):
    store = Store.for_home(config.home)
    try:
        endpoint, baseline = _baseline(config, store, "jobs-api")
        data = json.loads(endpoint.fixture.read_text())
        for job in data["jobs"]:
            del job["title"]
        result = synthetic_result("jobs-api", body=json.dumps(data).encode())
        findings = run_rules(endpoint, result, baseline)
        blocked = blocking_findings(findings)
        assert blocked and blocked[0].rule == "schema"
        assert any("title" in detail for detail in blocked[0].details)
    finally:
        store.close()


def test_empty_list_is_caught(config):
    store = Store.for_home(config.home)
    try:
        endpoint, baseline = _baseline(config, store, "jobs-api")
        data = json.loads(endpoint.fixture.read_text())
        data["jobs"] = []
        result = synthetic_result("jobs-api", body=json.dumps(data).encode())
        findings = run_rules(endpoint, result, baseline)
        assert {"schema", "count"} <= {finding.rule for finding in blocking_findings(findings)}
    finally:
        store.close()


def test_html_page_is_caught(config):
    store = Store.for_home(config.home)
    try:
        endpoint, baseline = _baseline(config, store, "jobs-api")
        result = synthetic_result(
            "jobs-api", body=b"<!doctype html><html>Access denied</html>", content_type="text/html"
        )
        findings = run_rules(endpoint, result, baseline)
        assert any(finding.rule == "content-type" for finding in blocking_findings(findings))
        assert looks_like_html(result.text)
    finally:
        store.close()


def test_invalid_json_is_caught(config):
    store = Store.for_home(config.home)
    try:
        endpoint, baseline = _baseline(config, store, "jobs-api")
        result = synthetic_result("jobs-api", body=b'{"jobs": [')
        findings = run_rules(endpoint, result, baseline)
        assert any(finding.rule == "json" for finding in blocking_findings(findings))
    finally:
        store.close()


def test_status_mismatch_is_caught(config):
    store = Store.for_home(config.home)
    try:
        endpoint, baseline = _baseline(config, store, "jobs-api")
        data = json.loads(endpoint.fixture.read_text())
        result = synthetic_result("jobs-api", body=json.dumps(data).encode(), status=503)
        findings = run_rules(endpoint, result, baseline)
        assert any(finding.rule == "status" for finding in blocking_findings(findings))
    finally:
        store.close()


def test_extra_fields_fail_when_endpoint_is_strict(config):
    import dataclasses

    store = Store.for_home(config.home)
    try:
        endpoint, baseline = _baseline(config, store, "jobs-api")
        strict = dataclasses.replace(endpoint, allow_extra_fields=False)
        data = json.loads(endpoint.fixture.read_text())
        for job in data["jobs"]:
            job["team"] = "platform"
        result = synthetic_result("jobs-api", body=json.dumps(data).encode())
        findings = run_rules(strict, result, baseline)
        assert any(finding.rule == "schema" for finding in blocking_findings(findings))
    finally:
        store.close()


def test_series_truncation_is_caught(openmeteo_config):
    import json

    from sentinel.core.fetch import synthetic_result
    from sentinel.core.fingerprint import fingerprint_result
    from sentinel.core.store import Store
    from sentinel.rules.deterministic import run_rules

    store = Store.for_home(openmeteo_config.home)
    try:
        endpoint = openmeteo_config.endpoint("weather")
        data = json.loads(endpoint.fixture.read_text())
        result = synthetic_result("weather", body=json.dumps(data).encode())
        snapshot = store.record_snapshot("weather", result, fingerprint_result(result))
        store.set_baseline("weather", snapshot.id)
        baseline = store.baseline("weather")

        truncated = json.loads(json.dumps(data))
        truncated["hourly"]["temperature_2m"] = truncated["hourly"]["temperature_2m"][:20]
        result = synthetic_result("weather", body=json.dumps(truncated).encode())
        findings = run_rules(endpoint, result, baseline)
        count = next(finding for finding in findings if finding.rule == "count")
        assert not count.ok
        assert any("hourly.temperature_2m" in detail for detail in count.details)
    finally:
        store.close()


def test_watch_change_is_caught(openmeteo_config):
    import json

    from sentinel.core.fetch import synthetic_result
    from sentinel.core.store import Store
    from sentinel.rules.deterministic import blocking_findings, run_rules
    from sentinel.watch import fingerprint_with_watch

    config_path = openmeteo_config.path.parent / "llm-prices.yaml"
    from sentinel.config import load_config

    config = load_config(config_path)
    endpoint = config.endpoint("openrouter-models")
    store = Store.for_home(config.home)
    try:
        data = json.loads(endpoint.fixture.read_text())
        result = synthetic_result("openrouter-models", body=json.dumps(data).encode())
        snapshot = store.record_snapshot(
            "openrouter-models", result, fingerprint_with_watch(endpoint, result)
        )
        store.set_baseline("openrouter-models", snapshot.id)
        baseline = store.baseline("openrouter-models")

        baseline_price = data["data"][0]["pricing"]["prompt"]
        data["data"][0]["pricing"]["prompt"] = "9.99"
        result = synthetic_result("openrouter-models", body=json.dumps(data).encode())
        findings = run_rules(endpoint, result, baseline)
        blocked = blocking_findings(findings)
        assert blocked and blocked[0].rule == "watch"
        assert any("pricing.prompt" in detail for detail in blocked[0].details)
        assert any(baseline_price in detail for detail in blocked[0].details)
    finally:
        store.close()
