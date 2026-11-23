"""Build the state text Kev sees for an endpoint.

Kev reads one text state per request. The default template includes the endpoint
identity, the HTTP metadata, and a sampled payload — sampling matters because a
100-record response is mostly redundant for a judgement and costs tokens/latency.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import EndpointConfig
from ..errors import ConfigError
from .fetch import FetchResult

MAX_SCALAR_LIST = 20


def _sample(value: Any, sample_records: int) -> Any:
    if isinstance(value, dict):
        return {key: _sample(child, sample_records) for key, child in value.items()}
    if isinstance(value, list):
        if value and any(isinstance(item, dict) for item in value):
            return [_sample(item, sample_records) for item in value[:sample_records]]
        return [_sample(item, sample_records) for item in value[:MAX_SCALAR_LIST]]
    return value


def payload_text(result: FetchResult, sample_records: int, max_chars: int) -> str:
    if result.json is not None:
        rendered = json.dumps(_sample(result.json, sample_records), indent=2, ensure_ascii=False, default=str)
    else:
        rendered = result.text
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars].rstrip() + "\n... [truncated]"
    return rendered


def _request_text(endpoint: EndpointConfig) -> str:
    if endpoint.body is None:
        return ""
    if isinstance(endpoint.body, (dict, list)):
        return json.dumps(endpoint.body, indent=2, ensure_ascii=False)
    return str(endpoint.body)


def build_state(endpoint: EndpointConfig, result: FetchResult, *, sample_records: int, max_chars: int) -> str:
    values = {
        "name": endpoint.name,
        "url": endpoint.url or (str(endpoint.fixture) if endpoint.fixture else ""),
        "status": result.status if result.status is not None else "n/a",
        "content_type": result.content_type or "unknown",
        "payload": payload_text(result, sample_records, max_chars),
        "body": payload_text(result, sample_records, max_chars),
        "request": _request_text(endpoint),
    }
    try:
        return endpoint.state.format(**values).strip()
    except (KeyError, ValueError, IndexError) as exc:
        raise ConfigError(
            f"endpoint {endpoint.name!r}: state template failed ({exc}); "
            "literal braces must be doubled and only {name} {url} {status} "
            "{content_type} {payload} {body} are available"
        ) from exc