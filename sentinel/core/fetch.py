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

    try:
        with httpx.Client(
            timeout=timeout_s, follow_redirects=True, transport=transport
        ) as client:
            response = client.request(endpoint.method, endpoint.url, headers=dict(endpoint.headers))
        body = response.content
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