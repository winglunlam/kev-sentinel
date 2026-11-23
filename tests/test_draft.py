"""Optional LLM-assisted drafting: validated, opt-in, mocked provider."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import sentinel.cli as cli
from sentinel.config import ConfigError, load_config
from sentinel.draft import validate_draft

from .conftest import copy_fixture

DRAFT = {
    "questions": {
        "duties_present": {
            "type": "noul",
            "instructions": "Do the job descriptions mention duties or responsibilities?",
            "fail_if": "p < 0.55",
        },
        "currency_usd": {
            "type": "choice",
            "instructions": "Are the salaries in US dollars, another currency, or unclear?",
            "criteria": {"usd": "US dollars", "other": "Another currency", "unclear": "Unclear"},
            "fail_if": "choice != 'usd'",
        },
    }
}


class _DraftProvider(BaseHTTPRequestHandler):
    response_text = json.dumps(DRAFT)
    requests: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        _DraftProvider.requests.append(payload)
        content = json.dumps({"choices": [{"message": {"role": "assistant", "content": self.response_text}}]})
        body = content.encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def draft_provider(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DraftProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SENTINEL_DRAFT_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}",)
    monkeypatch.setenv("SENTINEL_DRAFT_API_KEY", "test-key")
    monkeypatch.setenv("SENTINEL_DRAFT_MODEL", "fake-model")
    try:
        yield
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_validate_draft_parses_and_sanitises():
    specs = validate_draft({"questions": {"Not Valid Id!": {
        "type": "noul", "instructions": "Is it fine?", "fail_if": "p < 0.5",
    }}})
    assert list(specs) == ["not_valid_id"]
    assert specs["not_valid_id"]["fail_if"] == "p < 0.5"


def test_validate_draft_rejects_bad_condition():
    with pytest.raises(ConfigError):
        validate_draft({"questions": {"q": {"type": "noul", "instructions": "x", "fail_if": "sometimes"}}})


def test_init_llm_draft(tmp_path, monkeypatch, draft_provider, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    _DraftProvider.requests.clear()
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture), "--llm-draft"]) == 0
    output = capsys.readouterr().out
    assert "drafted" in output and "fake-model" in output
    config = load_config(tmp_path / "sentinel.yaml")
    endpoint = config.endpoint("jobs-api")
    assert {"duties_present", "currency_usd", "error_page"} <= set(endpoint.questions)
    sent = _DraftProvider.requests[0]
    assert sent["model"] == "fake-model"
    assert "job" in sent["messages"][1]["content"].lower()


def test_init_llm_draft_rejects_bad_model_output(tmp_path, monkeypatch, draft_provider, capsys):
    monkeypatch.setattr(_DraftProvider, "response_text", "I cannot help with that.")
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture), "--llm-draft"]) == 2
    assert "did not return JSON" in capsys.readouterr().out
    assert not (tmp_path / "sentinel.yaml").exists()


def test_init_llm_draft_needs_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SENTINEL_DRAFT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture), "--llm-draft"]) == 2
    assert "SENTINEL_DRAFT_API_KEY" in capsys.readouterr().out
