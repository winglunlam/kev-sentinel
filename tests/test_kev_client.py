from __future__ import annotations

import json

import httpx
import pytest

from sentinel.config import Question, SemConfig, load_config
from sentinel.errors import KevError
from sentinel.sem.client import KevClient, probe

from .conftest import EXAMPLES


def _questions():
    config = load_config(EXAMPLES / "sentinel.yaml", ) if False else load_config(EXAMPLES / "sentinel.yaml")
    return list(config.endpoint("status-page").questions.values())


ANSWER_PAYLOAD = {
    "model": "kev-latest",
    "answers": {
        "services_list": {"type": "noul", "noul": 0.96},
    },
    "latency_ms": 12.5,
}


def test_one_batched_request():
    questions = _questions()[:1]
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/systemone"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=ANSWER_PAYLOAD)

    client = KevClient(SemConfig(base_url="http://kev.test"), transport=httpx.MockTransport(handler))
    response = client.answer_many("state text", questions)
    assert len(requests) == 1
    assert requests[0]["state"] == "state text"
    assert list(requests[0]["questions"]) == ["services_list"]
    assert response.answers["services_list"].value == pytest.approx(0.96)
    assert response.latency_ms == pytest.approx(12.5)


def test_choice_and_score_parsing(examples_config):
    config = examples_config
    questions = list(config.endpoint("pricing-api").questions.values())
    payload = {
        "answers": {
            "products_list": {"type": "noul", "noul": 0.9},
            "usd_prices": {"type": "noul", "noul": 0.8},
        }
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    # add a choice and score question for the parser
    questions.append(
        Question(
            qid="currency",
            type="choice",
            instructions="currency?",
            fail_if=config.endpoint("pricing-api").questions["usd_prices"].fail_if,
            criteria={"usd": None, "eur": None},
        )
    )
    payload["answers"]["currency"] = {
        "type": "choice",
        "choice": "usd",
        "confidence": 0.77,
        "probabilities": {"usd": 0.8, "eur": 0.2},
    }
    client = KevClient(SemConfig(base_url="http://kev.test"), transport=httpx.MockTransport(handler))
    response = client.answer_many("state", questions)
    answer = response.answers["currency"]
    assert answer.value == "usd"
    assert answer.confidence == pytest.approx(0.77)
    assert answer.metric("p") == pytest.approx(0.8)


def test_server_error_raises_after_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    client = KevClient(
        SemConfig(base_url="http://kev.test", timeout_s=2), transport=httpx.MockTransport(handler), max_attempts=2
    )
    with pytest.raises(KevError, match="HTTP 500"):
        client.answer_many("state", _questions()[:1])
    assert calls["n"] == 2


def test_client_error_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, text="bad question")

    client = KevClient(SemConfig(base_url="http://kev.test"), transport=httpx.MockTransport(handler))
    with pytest.raises(KevError, match="400"):
        client.answer_many("state", _questions()[:1])
    assert calls["n"] == 1


def test_missing_answer_raises():
    def handler(request):
        return httpx.Response(200, json={"answers": {}})

    client = KevClient(SemConfig(base_url="http://kev.test"), transport=httpx.MockTransport(handler))
    with pytest.raises(KevError, match="did not answer"):
        client.answer_many("state", _questions()[:1])


def test_separate_mode_sends_one_request_per_question():
    requests: list[dict] = []
    questions = _questions()

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        qid = next(iter(payload["questions"]))
        values = {
            "services_list": {"type": "noul", "noul": 0.9},
            "all_operational": {"type": "noul", "noul": 0.9},
            "reporting_outage": {"type": "noul", "noul": 0.1},
        }
        return httpx.Response(200, json={"answers": {qid: values[qid]}})

    client = KevClient(
        SemConfig(base_url="http://kev.test", separate=True), transport=httpx.MockTransport(handler)
    )
    response = client.answer_many("state", questions)
    assert len(requests) == len(questions)
    assert set(response.answers) == {question.qid for question in questions}


def test_probe_returns_models():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"object": "list", "data": [{"id": "kev-latest"}]})

    data = probe(SemConfig(base_url="http://kev.test"), transport=httpx.MockTransport(handler))
    assert data["data"][0]["id"] == "kev-latest"
