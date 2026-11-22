from __future__ import annotations

import json

from sentinel.check import run_check
from sentinel.core.fetch import synthetic_result
from sentinel.core.fingerprint import fingerprint_result
from sentinel.core.store import Store
from sentinel.errors import KevError

from .conftest import FakeJudge, choice, noul, read_json, write_json


def _setup(config, store: Store, name: str = "jobs-api"):
    endpoint = config.endpoint(name)
    data = read_json(endpoint.fixture)
    result = synthetic_result(name, body=json.dumps(data).encode())
    snapshot = store.record_snapshot(name, result, fingerprint_result(result))
    store.set_baseline(name, snapshot.id)
    return endpoint


def _good_judge() -> FakeJudge:
    return FakeJudge(
        fn=lambda qid, state: choice(qid, "usd") if qid == "salary_currency" else noul(qid, 0.9)
    )


def test_clean_run_passes(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _setup(config, store)
        result = run_check(config, endpoint, store, _good_judge(), alerts=False)
        assert result.ok
        assert result.reasons == []
        assert store.recent_runs(1)
        assert store.latest_snapshot(endpoint.name) is not None
    finally:
        store.close()


def test_semantic_drift_fails_without_schema_change(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _setup(config, store)
        data = read_json(endpoint.fixture)
        for job in data["jobs"]:
            job["description"] = "Best deals on luxury watches, diet pills, and cheap holidays."
        write_json(endpoint.fixture, data)

        def judge_fn(qid, state):
            if qid == "salary_currency":
                return choice(qid, "usd")
            return noul(qid, 0.1 if "luxury watches" in state else 0.9)

        result = run_check(config, endpoint, store, FakeJudge(fn=judge_fn), alerts=False)
        assert not result.ok
        assert any("job_duties" in reason for reason in result.reasons)
        schema = next(finding for finding in result.findings if finding.rule == "schema")
        assert schema.ok
    finally:
        store.close()


def test_structural_drift_fails_in_rules(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _setup(config, store)
        data = read_json(endpoint.fixture)
        for job in data["jobs"]:
            del job["title"]
        write_json(endpoint.fixture, data)
        result = run_check(config, endpoint, store, _good_judge(), alerts=False)
        assert not result.ok
        assert any("removed field" in reason for reason in result.reasons)
    finally:
        store.close()


def test_kev_unavailable_sets_semantic_error(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _setup(config, store)

        class BrokenJudge:
            def answer_many(self, state, questions):
                raise KevError("connection refused")

        result = run_check(config, endpoint, store, BrokenJudge(), alerts=False)
        assert not result.ok
        assert result.semantic_error and "connection refused" in result.semantic_error
    finally:
        store.close()


def test_alert_on_transition_only(config):
    store = Store.for_home(config.home)
    try:
        endpoint = _setup(config, store)
        calls: list[bool] = []

        import sentinel.check as check_module
        from sentinel.alert import AlertOutcome

        original = check_module.maybe_alert

        def fake_alert(config, endpoint, result, *, previous_ok, store, **kwargs):
            calls.append(previous_ok)
            if previous_ok is False:
                return AlertOutcome(False, "still failing")
            return AlertOutcome(True)

        check_module.maybe_alert = fake_alert
        try:
            first = run_check(config, endpoint, store, _good_judge(), alerts=True)
            assert first.ok and not first.alert_sent

            data = read_json(endpoint.fixture)
            for job in data["jobs"]:
                job["description"] = "Best deals on luxury watches."
            write_json(endpoint.fixture, data)

            def judge_fn(qid, state):
                if qid == "salary_currency":
                    return choice(qid, "usd")
                return noul(qid, 0.1 if "luxury watches" in state else 0.9)

            second = run_check(config, endpoint, store, FakeJudge(fn=judge_fn), alerts=True)
            assert not second.ok and second.alert_sent
            third = run_check(config, endpoint, store, FakeJudge(fn=judge_fn), alerts=True)
            assert not third.ok and not third.alert_sent
            assert calls == [True, False]
        finally:
            check_module.maybe_alert = original
    finally:
        store.close()
