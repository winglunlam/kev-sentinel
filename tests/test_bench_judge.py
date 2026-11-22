"""The cloud baseline adapter for bench/: protocol and round-trip, no provider."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from .conftest import EXAMPLES

REPO = Path(__file__).resolve().parents[1]
JUDGE_PATH = REPO / "bench" / "judge_openai.py"
BENCH_PATH = REPO / "bench" / "bench.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


judge_openai = _load("judge_openai", JUDGE_PATH)
bench = _load("bench_bench", BENCH_PATH)


def _questions():
    from sentinel.config import load_config

    config = load_config(EXAMPLES / "sentinel.yaml")
    return config.endpoint("status-page").questions


def test_build_messages_contains_every_question():
    payload = {
        "state": "the payload",
        "questions": {
            "q1": {"type": "noul", "instructions": "is it ok?"},
            "q2": {"type": "choice", "criteria": {"ok": None, "bad": None}},
        },
    }
    messages = judge_openai.build_messages(payload)
    assert messages[0]["role"] == "system"
    text = messages[1]["content"]
    assert "q1" in text and "q2" in text
    assert "is it ok?" in text and "bad" in text


def test_parse_answers_accepts_the_system_one_shape():
    questions = {
        "n": {"type": "noul"},
        "c": {"type": "choice", "criteria": {"ok": None, "bad": None}},
        "s": {"type": "score"},
    }
    raw = json.dumps(
        {
            "answers": {
                "n": {"type": "noul", "noul": 1.4},
                "c": {"type": "choice", "choice": "ok", "probabilities": {"ok": 0.9, "bad": 0.1}},
                "s": {"type": "score", "score": 1.5, "confidence": 0.4},
            }
        }
    )
    answers = judge_openai.parse_answers(raw, questions)
    assert answers["n"]["noul"] == 1.0
    assert answers["c"]["choice"] == "ok"
    assert answers["s"]["score"] == 1.5


def test_parse_answers_rejects_unknown_choice():
    questions = {"c": {"type": "choice", "criteria": {"ok": None}}}
    raw = json.dumps({"answers": {"c": {"type": "choice", "choice": "nope"}}})
    with pytest.raises(ValueError, match="not one of the criteria"):
        judge_openai.parse_answers(raw, questions)


class _FakeProvider(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.requests.append(payload)
        questions = _questions()
        answers = {}
        for qid, question in questions.items():
            if question.type == "noul":
                answers[qid] = {"type": "noul", "noul": 0.42}
            elif question.type == "choice":
                first = next(iter(question.criteria))
                answers[qid] = {
                    "type": "choice",
                    "choice": first,
                    "confidence": 0.8,
                    "probabilities": {first: 0.9},
                }
            else:
                answers[qid] = {"type": "score", "score": 1.0, "confidence": 0.5}
        content = json.dumps({"answers": answers})
        body = json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def fake_provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_subprocess_judge_round_trip(fake_provider, monkeypatch):
    questions = list(_questions().values())
    command = f"{sys.executable} {JUDGE_PATH}"
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", fake_provider)
    monkeypatch.setenv("OPENAI_MODEL", "fake-model")
    judge = bench.SubprocessJudge(command)
    response = judge.answer_many("state text", questions)
    assert set(response.answers) == {question.qid for question in questions}
    assert response.answers["services_list"].value == pytest.approx(0.42)
    assert _FakeProvider.requests
    assert _FakeProvider.requests[0]["model"] == "fake-model"
    assert _FakeProvider.requests[0]["messages"][0]["role"] == "system"
