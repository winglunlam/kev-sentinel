from __future__ import annotations

import pytest
from sentinel.config import Condition, ConfigError, load_config, starter_config
from sentinel.sem.types import Answer
from sentinel.check import run_check
from sentinel.core.store import Store

from .conftest import EXAMPLES, copy_fixture, write_config


def _answer(qtype: str, value):
    return Answer(qid="q", qtype=qtype, value=value, confidence=0.9, probabilities={str(value): 0.9})


def test_noul_condition_is_a_failure_condition():
    cond = Condition.parse("p < 0.85", "noul", "q")
    assert cond.check(_answer("noul", 0.95))[0] is True
    assert cond.check(_answer("noul", 0.40))[0] is False


def test_expect_false_condition():
    cond = Condition.parse("p > 0.15", "noul", "q")
    assert cond.check(_answer("noul", 0.02))[0] is True
    assert cond.check(_answer("noul", 0.80))[0] is False


def test_choice_condition():
    cond = Condition.parse("choice != 'usd'", "choice", "q")
    assert cond.check(_answer("choice", "usd"))[0] is True
    assert cond.check(_answer("choice", "eur"))[0] is False


def test_choice_metrics_are_validated():
    with pytest.raises(ConfigError):
        Condition.parse("choice >= 0.5", "choice", "q")
    with pytest.raises(ConfigError):
        Condition.parse("score < 0.5", "noul", "q")
    with pytest.raises(ConfigError):
        Condition.parse("p < high", "noul", "q")


def test_examples_config_loads():
    config = load_config(EXAMPLES / "sentinel.yaml")
    assert [endpoint.name for endpoint in config.endpoints] == [
        "jobs-api",
        "pricing-api",
        "weather-forecast",
        "news-feed",
        "status-page",
    ]
    assert len(config.endpoints[0].questions) == 3
    for endpoint in config.endpoints:
        assert endpoint.fixture is not None and endpoint.fixture.is_file()


def test_unknown_key_is_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """\
version: 1
endpoints:
  - name: x
    fixture: f.json
    questionz: {}
""",
    )
    (tmp_path / "f.json").write_text("{}")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)


def test_unknown_state_placeholder_is_rejected(tmp_path):
    fixture = copy_fixture(tmp_path)
    path = write_config(
        tmp_path,
        f"""\
version: 1
endpoints:
  - name: jobs
    fixture: {fixture.name}
    state: "{{bogus}}"
    questions:
      q:
        type: noul
        instructions: ok?
        fail_if: "p < 0.5"
""",
    )
    with pytest.raises(ConfigError, match="unknown placeholder"):
        load_config(path)


def test_duplicate_endpoint_names_rejected(tmp_path):
    fixture = copy_fixture(tmp_path)
    question = """\
      q:
        type: noul
        instructions: Is this fine?
        fail_if: "p < 0.5"
"""
    path = write_config(
        tmp_path,
        f"""\
version: 1
endpoints:
  - name: jobs
    fixture: {fixture.name}
    questions:
{question}
  - name: jobs
    fixture: {fixture.name}
    questions:
{question}
""",
    )
    with pytest.raises(ConfigError, match="duplicate endpoint"):
        load_config(path)


def test_starter_config_round_trips(tmp_path):
    fixture = copy_fixture(tmp_path)
    text = starter_config("jobs-api", fixture=fixture, fixture_ref=fixture.name)
    path = write_config(tmp_path, text)
    config = load_config(path)
    endpoint = config.endpoint("jobs-api")
    assert endpoint.fixture == fixture
    assert set(endpoint.questions) == {"expected_content", "error_page"}


def test_unknown_endpoint_suggests_a_close_match(config):
    with pytest.raises(ConfigError, match="did you mean 'jobs-api'"):
        config.endpoint("jobs-ap")


def test_missing_baseline_raises(config, config_dir):
    store = Store.for_home(config.home)
    try:
        endpoint = config.endpoint("jobs-api")
        from sentinel.errors import BaselineError

        with pytest.raises(BaselineError):
            run_check(config, endpoint, store, judge=None)
    finally:
        store.close()


def test_env_override_for_kev_url(tmp_path, monkeypatch):
    fixture = copy_fixture(tmp_path)
    path = write_config(
        tmp_path,
        f"""\
version: 1
endpoints:
  - name: jobs
    fixture: {fixture.name}
    questions:
      q:
        type: noul
        instructions: Is this fine?
        fail_if: "p < 0.5"
""",
    )
    monkeypatch.setenv("SENTINEL_KEV_BASE_URL", "http://10.0.0.5:9000")
    config = load_config(path)
    assert config.sem.base_url == "http://10.0.0.5:9000"
