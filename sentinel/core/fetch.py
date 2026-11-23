"""Fetch an endpoint, or read a recorded fixture.

``fixture`` exists for two reasons: examples that run offline, and CI tests that
must not depend on a third-party site staying up. A fixture is not a mock — it is
a recorded response body, and rules and questions run against it unchanged.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import EndpointConfig

MAX_STREAM_BYTES = 256 * 1024


@dataclass(frozen=True)
class FetchResult:
    endpoint: str
    ok: bool
    status: int | None
    content_type: str
    body: bytes
    text: str
    json: Any | None
    json_error: str | None
    error: str | None
    elapsed_ms: int
    source: str  # "url" | "fixture" | "chaos"

    @property
    def size(self) -> int:
        return len(self.body)


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse a text/event-stream body into ``[{"event": ..., "data": ...}]``.

    Comment lines (``: ping``) are keep-alives and are dropped. ``data:`` values
    that parse as JSON are stored as objects; anything else stays a string.
    """
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        payload = "\n".join(current["data"]).strip()
        data: Any = payload
        if payload:
            try:
                data = json.loads(payload)
            except ValueError:
                data = payload
        events.append({"event": current["event"], "data": data})
        current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            flush()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            if current is None:
                current = {"event": "message", "data": []}
            current["event"] = line[len("event:"):].strip() or "message"
        elif line.startswith("data:"):
            if current is None:
                current = {"event": "message", "data": []}
            current["data"].append(line[len("data:"):].lstrip())
    flush()
    return events


_SSE_DONE = ("event: done", "event: complete", "event: completed", "event: finish",
             "event: end", "event: message_stop", "data: [DONE]")


def _sse_finished(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SSE_DONE)


def _parse_json(text: str) -> tuple[Any | None, str | None]:
    if not text.strip():
        return None, "empty body"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _result(
    endpoint: str,
    *,
    ok: bool,
    status: int | None,
    content_type: str,
    body: bytes,
    error: str | None,
    elapsed_ms: int,
    source: str,
) -> FetchResult:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with errors="replace" cannot fail
        text = ""
    if "text/event-stream" in content_type.lower():
        events = parse_sse(text)
        parsed = {"events": events} if events else None
        json_error = None if events else "empty event stream"
    else:
        parsed, json_error = _parse_json(text)
    return FetchResult(
        endpoint=endpoint,
        ok=ok,
        status=status,
        content_type=content_type,
        body=body,
        text=text,
        json=parsed,
        json_error=json_error,
        error=error,
        elapsed_ms=elapsed_ms,
        source=source,
    )


def _fixture_type(path: Path) -> str:
    return "application/json" if path.suffix.lower() in {".json", ".jsonl"} else "text/plain"


def fetch_endpoint(
    endpoint: EndpointConfig,
    *,
    timeout_s: float,
    live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> FetchResult:
    """Fetch ``endpoint``; a configured fixture is used unless ``live`` is set."""
    started = time.perf_counter()
    if endpoint.fixture is not None and not live:
        try:
            body = endpoint.fixture.read_bytes()
        except OSError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return _result(
                endpoint.name,
                ok=False,
                status=None,
                content_type="",
                body=b"",
                error=f"cannot read fixture {endpoint.fixture}: {exc}",
                elapsed_ms=elapsed,
                source="fixture",
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        return _result(
            endpoint.name,
            ok=True,
            status=200,
            content_type=_fixture_type(endpoint.fixture),
            body=body,
            error=None,
            elapsed_ms=elapsed,
            source="fixture",
        )

    if not endpoint.url:
        elapsed = int((time.perf_counter() - started) * 1000)
        return _result(
            endpoint.name,
            ok=False,
            status=None,
            content_type="",
            body=b"",
            error="no url configured and fixture is disabled",
            elapsed_ms=elapsed,
            source="url",
        )

    headers = dict(endpoint.headers)
    request_kwargs: dict[str, Any] = {}
    if endpoint.body is not None:
        if isinstance(endpoint.body, (dict, list)):
            request_kwargs["json"] = endpoint.body
        else:
            raw_body = str(endpoint.body)
            try:
                request_kwargs["json"] = json.loads(raw_body)
            except ValueError:
                request_kwargs["content"] = raw_body.encode("utf-8")
                headers.setdefault("content-type", "text/plain; charset=utf-8")

    stream_timeout = endpoint.stream_timeout_s or timeout_s
    # POST bodies are usually calls that stream or generate; the read timeout is
    # the stream budget so a quiet event stream does not hold a check open for the
    # full connect timeout. Plain GETs keep the normal read timeout.
    read_timeout = stream_timeout if (endpoint.body is not None or endpoint.method != "GET") else timeout_s
    client_timeout = httpx.Timeout(
        connect=timeout_s, read=max(0.5, read_timeout), write=timeout_s, pool=timeout_s
    )
    try:
        with httpx.Client(
            timeout=client_timeout, follow_redirects=True, transport=transport
        ) as client:
            with client.stream(
                endpoint.method, endpoint.url, headers=headers, **request_kwargs
            ) as response:
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type.lower():
                    # A chat stream can stay open indefinitely. Read until the
                    # stream completes, the byte cap is hit, or the deadline passes,
                    # and keep whatever events arrived.
                    deadline = time.monotonic() + stream_timeout
                    chunks: list[bytes] = []
                    total = 0
                    try:
                        for chunk in response.iter_bytes():
                            chunks.append(chunk)
                            total += len(chunk)
                            if total >= MAX_STREAM_BYTES:
                                break
                            if time.monotonic() >= deadline:
                                break
                            if _sse_finished(b"".join(chunks).decode("utf-8", errors="replace")):
                                break
                    except httpx.TimeoutException:
                        # An open SSE stream can go quiet (keep-alive interval longer
                        # than the read timeout). Keep the events collected so far.
                        if not chunks:
                            raise
                    body = b"".join(chunks)
                else:
                    body = response.read()
        elapsed = int((time.perf_counter() - started) * 1000)
        return _result(
            endpoint.name,
            ok=True,
            status=response.status_code,
            content_type=response.headers.get("content-type", ""),
            body=body,
            error=None,
            elapsed_ms=elapsed,
            source="url",
        )
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return _result(
            endpoint.name,
            ok=False,
            status=None,
            content_type="",
            body=b"",
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=elapsed,
            source="url",
        )


def synthetic_result(
    endpoint: str, *, body: bytes, status: int = 200, content_type: str = "application/json"
) -> FetchResult:
    """A FetchResult for an in-memory body (used by chaos injection and tests)."""
    return _result(
        endpoint,
        ok=True,
        status=status,
        content_type=content_type,
        body=body,
        error=None,
        elapsed_ms=0,
        source="chaos",
    )