from __future__ import annotations

import json

from sentinel.calibrate import _suggest, run_calibration
from sentinel.config import Condition, Question
from sentinel.core.fetch import synthetic_result
from sentinel.core.fingerprint import fingerprint_result
from sentinel.core.store import Store

from .conftest import FakeJudge, choice, noul, read_json


def _question(qid: str, qtype: str = "noul", fail_if: str = "p < 0.5") -> Question:
    return Question(
        qid=qid,
        type=qtype,
        instructions="test?",
        fail_if=Condition.parse(fail_if, qtype, qid),
        criteria={"usd": None, "other": None} if qtype == "choice" else None,
    )


def test_suggest_low_threshold():
    suggested, separation, _, worst = _suggest(
        _question("q"), noul("q", 0.9), [("wrong-entity", noul("q", 0.2)), ("noise", noul("q", 0.85))]
    )
    assert suggested == "p < 0.55"
    assert separation > 0.5
    assert worst.startswith("wrong-entity")


def test_suggest_high_threshold_for_expect_false():
    suggested, separation, _, _ = _suggest(
        _question("q", fail_if="p > 0.4"), noul("q", 0.03), [("status-flip", noul("q", 0.95))]
    )
    assert suggested == "p > 0.5"
    assert separation > 0.8


def test_suggest_choice_flip():
    suggested, _, _, worst = _suggest(
        _question("q", "choice", "choice != 'usd'"),
        choice("q", "usd"),
        [("currency-swap", choice("q", "eur"))],
    )
    assert suggested == "choice != 'usd'"
    assert "eur" in worst


def test_no_separation_is_reported():
    suggested, separation, note, _ = _suggest(
        _question("q"), noul("q", 0.9), [("status-flip", noul("q", 0.88))]
    )
    assert suggested is None
    assert "no separation" in note


def test_run_calibration_end_to_end(config):
    store = Store.for_home(config.home)
    try:
        endpoint = config.endpoint("jobs-api")
        data = read_json(endpoint.fixture)
        result = synthetic_result("jobs-api", body=json.dumps(data).encode())
        snapshot = store.record_snapshot("jobs-api", result, fingerprint_result(result))
        store.set_baseline("jobs-api", snapshot.id)

        def judge_fn(qid, state):
            if qid == "salary_currency":
                return choice(qid, "eur" if "EUR" in state else "usd")
            return noul(qid, 0.15 if "luxury watches" in state else 0.9)

        rows = run_calibration(config, endpoint, store, FakeJudge(fn=judge_fn))
        by_question = {row.question: row for row in rows}
        assert by_question["job_duties"].suggested_fail_if
        assert by_question["job_duties"].suggested_fail_if.startswith("p <")
        assert by_question["salary_currency"].suggested_fail_if == "choice != 'usd'"
        assert all(row.shots for row in rows)
    finally:
        store.close()
