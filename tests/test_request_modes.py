"""POST bodies, SSE streams, and init refusing to baseline an error response."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
import pytest
import yaml

import sentinel.cli as cli
from sentinel.config import EndpointConfig, load_config, starter_config
from sentinel.core.fetch import fetch_endpoint, parse_sse
from sentinel.suggest import suggest_questions

from .conftest import write_config


def test_parse_sse_drops_comments_and_parses_data():
    body = (
        "event: connect\ndata: {\"connect\": true}\n\n"
        ": ping\n\n"
        "event: text\ndata: hello\n\n"
        "data: [DONE]\n\n"
    )
    events = parse_sse(body)
    assert events[0] == {"event": "connect", "data": {"connect": True}}
    assert events[1] == {"event": "text", "data": "hello"}
    assert events[2] == {"event": "message", "data": "[DONE]"}


def test_fetch_posts_json_body_and_parses_sse():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='event: connect\ndata: {"connect": true}\n\nevent: text\ndata: {"delta": "hi"}\n\n',
        )

    endpoint = EndpointConfig(
        name="chat",
        questions={},
        url="http://chat.test/chat",
        method="POST",
        body={"messages": [{"role": "user", "content": "hi"}]},
    )
    result = fetch_endpoint(endpoint, timeout_s=5, transport=httpx.MockTransport(handler))
    assert seen["method"] == "POST"
    assert seen["body"]["messages"][0]["content"] == "hi"
    assert "application/json" in seen["content_type"]
    assert result.ok
    assert result.json["events"][0]["data"] == {"connect": True}
    assert result.json["events"][1]["data"] == {"delta": "hi"}


def test_fetch_sends_raw_string_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(200, json={"ok": True})

    endpoint = EndpointConfig(
        name="plain",
        questions={},
        url="http://plain.test/post",
        method="POST",
        body="hello=world",
    )
    fetch_endpoint(endpoint, timeout_s=5, transport=httpx.MockTransport(handler))
    assert seen["body"] == b"hello=world"
    assert "text/plain" in seen["content_type"]


def test_config_round_trips_request_shape(tmp_path):
    path = write_config(
        tmp_path,
        """\
version: 1
endpoints:
  - name: chat
    url: http://localhost:9999/chat/
    method: POST
    stream_timeout_s: 20
    headers:
      authorization: Bearer test
    body:
      messages:
        - role: user
          content: hi
    questions:
      q:
        type: noul
        instructions: Does this contain a reply?
        fail_if: "p < 0.6"
""",
    )
    endpoint = load_config(path).endpoint("chat")
    assert endpoint.method == "POST"
    assert endpoint.body["messages"][0]["content"] == "hi"
    assert endpoint.headers["authorization"] == "Bearer test"
    assert endpoint.stream_timeout_s == 20.0


def test_starter_config_writes_post_shape(tmp_path):
    text = starter_config(
        "chat",
        url="http://localhost:9999/chat/",
        method="POST",
        body={"messages": [{"role": "user", "content": "hi"}]},
        headers={"authorization": "Bearer t"},
        questions={},
        stream_timeout_s=20.0,
    )
    document = yaml.safe_load(text)
    endpoint = document["endpoints"][0]
    assert endpoint["method"] == "POST"
    assert endpoint["body"]["messages"][0]["content"] == "hi"
    assert endpoint["headers"]["authorization"] == "Bearer t"
    assert endpoint["stream_timeout_s"] == 20.0


def test_stream_payload_gets_a_reply_question():
    payload = {"events": [{"event": "connect", "data": {"connect": True}}]}
    suggestions = suggest_questions(payload, endpoint_name="chat-agent")
    assert [item.qid for item in suggestions] == ["stream_reply"]
    assert "assistant reply" in suggestions[0].question.instructions


class _ChatServer(BaseHTTPRequestHandler):
    snippet = "hello"
    handshake_only = False

    def _write(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        body = b'{"detail":"Method Not Allowed"}'
        self.send_response(405)
        self.send_header("allow", "POST")
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        events = 'event: connect\ndata: {"connect": true}\n\n'
        if not self.handshake_only:
            events += f'event: text\ndata: {{"delta": "{self.snippet}"}}\n\nevent: done\ndata: {{}}\n\n'
        self._write(200, "text/event-stream; charset=utf-8", events.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def chat_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/chat/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_init_refuses_to_baseline_a_405(tmp_path, monkeypatch, capsys, chat_server):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "sentinel.yaml"
    assert (
        cli.main(
            [
                "init",
                "chat-agent",
                "--url",
                chat_server,
                "--config",
                str(config),
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "not recording a baseline" in output
    assert "POST" in output
    assert not config.exists()


def test_init_posts_and_baselines_an_sse_endpoint(tmp_path, monkeypatch, capsys, chat_server):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "sentinel.yaml"
    body = '{"messages": [{"role": "user", "content": "hi"}]}'
    assert (
        cli.main(
            [
                "init",
                "chat-agent",
                "--url",
                chat_server,
                "--method",
                "POST",
                "--body",
                body,
                "--stream-timeout",
                "2",
                "--config",
                str(config),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "handshake" not in output
    assert config.exists()
    endpoint = load_config(config).endpoint("chat-agent")
    assert endpoint.method == "POST"
    assert endpoint.body["messages"][0]["content"] == "hi"
    assert endpoint.stream_timeout_s == 2.0
    assert "stream_reply" in endpoint.questions
    # the rules layer accepts the recorded event stream
    assert cli.main(["check", "--config", str(config), "--no-sem"]) == 0


def test_init_refuses_handshake_only_streams(tmp_path, monkeypatch, capsys, chat_server):
    monkeypatch.setattr(_ChatServer, "handshake_only", True)
    args = [
        "init",
        "chat-agent",
        "--url",
        chat_server,
        "--method",
        "POST",
        "--body",
        '{"messages": [{"role": "user", "content": "hi"}]}',
        "--stream-timeout",
        "2",
    ]
    try:
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "sentinel.yaml"
        assert cli.main(args + ["--config", str(config)]) == 2
        output = capsys.readouterr().out
        assert "not recording a baseline" in output
        assert "only connection/keep-alive" in output
        assert not config.exists()

        assert cli.main(args + ["--config", str(config), "--allow-handshake-only"]) == 0
        output = capsys.readouterr().out
        assert "handshake-only stream" in output
        assert config.exists()
        assert "stream_reply" not in load_config(config).endpoint("chat-agent").questions
    finally:
        monkeypatch.setattr(_ChatServer, "handshake_only", False)


def test_request_body_is_available_to_the_state(examples_config):
    from sentinel.core.fetch import synthetic_result
    from sentinel.core.state import build_state

    config_path = examples_config.path.parent / "agent-quality.yaml"
    config = load_config(config_path)
    endpoint = config.endpoint("support-agent")
    data = endpoint.fixture.read_bytes()
    result = synthetic_result("support-agent", body=data)
    state = build_state(endpoint, result, sample_records=config.sample_records, max_chars=config.max_state_chars)
    assert "How do I build my first bot?" in state
    assert "To build your first bot" in state
