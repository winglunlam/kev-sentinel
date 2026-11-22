from __future__ import annotations

import json

import pytest

from sentinel.chaos import MUTATORS, run_chaos
from sentinel.core.fetch import synthetic_result
from sentinel.core.fingerprint import compare_fingerprints, fingerprint_result
from sentinel.core.store import Store

from .conftest import FakeJudge, choice, noul


def _baseline(config, store: Store, name: str):
    endpoint = config.endpoint(name)
    data = json.loads(endpoint.fixture.read_text(encoding="utf-8"))
    result = synthetic_result(name, body=json.dumps(data).encode())
    snapshot = store.record_snapshot(name, result, fingerprint_result(result))
    store.set_baseline(name, snapshot.id)
    return endpoint


def test_every_mutator_runs(config):
    endpoint = config.endpoint("jobs-api")
    base = synthetic_result("jobs-api", body=json.dumps(json.loads(endpoint.fixture.read_text())).encode())
    for name, mutator in MUTATORS.items():
        mutation = mutator.apply(base, {}, name)
        assert mutation.body, name
        if name != "truncated" and mutation.content_type.startswith("application/json"):
            json.loads(mutation.body)


def test_wrong_entity_keeps_structure(config):
    endpoint = config.endpoint("jobs-api")
    base = synthetic_result("jobs-api", body=json.dumps(json.loads(endpoint.fixture.read_text())).encode())
    mutated = MUTATORS["wrong-entity"].apply(base, {}, "wrong-entity")
    before = fingerprint_result(base)
    after = fingerprint_result(synthetic_result("jobs-api", body=mutated.body))
    diff = compare_fingerprints(before, after)
    assert not diff.breaking
    assert before["stats"]["bytes"] == after["stats"]["bytes"]
    assert "luxury watches" in mutated.body.decode()


def test_status_flip_keeps_schema(examples_config):
    endpoint = examples_config.endpoint("status-page")
    base = synthetic_result("status-page", body=json.dumps(json.loads(endpoint.fixture.read_text())).encode())
    mutated = MUTATORS["status-flip"].apply(base, {}, "status-flip")
    before = fingerprint_result(base)
    after = fingerprint_result(synthetic_result("status-page", body=mutated.body))
    assert not compare_fingerprints(before, after).breaking
    assert b"outage" in mutated.body and b"down" in mutated.body


def test_field_rename_breaks_schema(config):
    endpoint = config.endpoint("jobs-api")
    base = synthetic_result("jobs-api", body=endpoint.fixture.read_bytes())
    mutated = MUTATORS["field-rename"].apply(base, {}, "field-rename")
    diff = compare_fingerprints(fingerprint_result(base), fingerprint_result(
        synthetic_result("jobs-api", body=mutated.body)
    ))
    assert diff.missing


def test_run_chaos_semantic_only_detection(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _baseline(config, store, "jobs-api")

        def judge_fn(qid: str, state: str):
            if qid == "salary_currency":
                return choice(qid, "others" if "EUR" in state else "usd")
            return noul(qid, 0.15 if "luxury watches" in state else 0.9)

        judge = FakeJudge(fn=judge_fn)
        results = run_chaos(config, endpoint, store, judge, names=["wrong-entity", "field-rename"])
        by_name = {result.mutation: result for result in results}
        assert by_name["wrong-entity"].rules_caught is False
        assert by_name["wrong-entity"].sem_caught is True
        assert by_name["wrong-entity"].detected is True
        assert by_name["field-rename"].rules_caught is True
    finally:
        store.close()


def test_run_chaos_unknown_mutation(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _baseline(config, store, "jobs-api")
        with pytest.raises(ValueError, match="unknown chaos mutation"):
            run_chaos(config, endpoint, store, FakeJudge(answers={}), names=["nope"])
    finally:
        store.close()


def test_run_chaos_requires_baseline(config):
    from sentinel.errors import BaselineError

    store = Store.for_home(config.home)
    try:
        endpoint = config.endpoint("jobs-api")
        with pytest.raises(BaselineError):
            run_chaos(config, endpoint, store, FakeJudge(answers={}), names=["empty-list"])
    finally:
        store.close()


def test_run_chaos_rules_only(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _baseline(config, store, "jobs-api")
        results = run_chaos(
            config, endpoint, store, None, names=["field-rename", "wrong-entity"]
        )
        by_name = {result.mutation: result for result in results}
        assert by_name["field-rename"].rules_caught is True
        assert by_name["field-rename"].sem_skipped == "disabled (--no-sem)"
        assert by_name["wrong-entity"].detected is False
    finally:
        store.close()
