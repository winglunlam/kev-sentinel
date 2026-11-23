"""Exact-value watches.

Some drift should never be judged by a model: a price, a plan limit, a currency
code, a version string. A watch records selected fields at baseline time and
reports any change beyond an optional relative tolerance. This is the
deterministic counterpart to a semantic question: use it when you can name the
exact field whose value must not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import EndpointConfig, WatchConfig
from .core.fingerprint import find_records

_MISSING = object()


@dataclass(frozen=True)
class WatchChange:
    path: str
    old: Any
    new: Any
    kind: str = "changed"  # "changed" | "removed"


def _get_path(node: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return _MISSING
    return node


def discover_watch_targets(endpoint: EndpointConfig, payload: Any) -> dict[str, Any]:
    """Flatten watched values into ``{"<item id> <field>": value}`` or ``{field: value}``."""
    watch = endpoint.watch
    if watch is None or payload is None:
        return {}
    values: dict[str, Any] = {}
    if watch.key:
        _path, records = find_records(payload)
        for item in records or []:
            if not isinstance(item, dict) or watch.key not in item:
                continue
            item_id = str(item[watch.key])
            for field in watch.fields:
                value = _get_path(item, field)
                if value is not _MISSING:
                    values[f"{item_id} {field}"] = value
        return values
    for field in watch.fields:
        value = _get_path(payload, field)
        if value is not _MISSING:
            values[field] = value
    return values


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def compare_watch(
    baseline: dict[str, Any], current: dict[str, Any], watch: WatchConfig
) -> list[WatchChange]:
    changes: list[WatchChange] = []
    for path in sorted(set(baseline) & set(current)):
        old, new = baseline[path], current[path]
        old_number, new_number = _as_number(old), _as_number(new)
        if old_number is not None and new_number is not None:
            scale = max(abs(old_number), abs(new_number), 1e-12)
            if abs(new_number - old_number) / scale > watch.tolerance:
                changes.append(WatchChange(path, old, new))
        elif old != new:
            changes.append(WatchChange(path, old, new))
    if watch.report_removed:
        for path in sorted(set(baseline) - set(current)):
            changes.append(WatchChange(path, baseline[path], None, "removed"))
    return changes


def fingerprint_with_watch(endpoint: EndpointConfig, result: Any) -> dict[str, Any]:
    """Fingerprint a response and attach the watched values for later comparison."""
    from .core.fingerprint import fingerprint_result

    fingerprint = fingerprint_result(result)
    if endpoint.watch is not None and result.json is not None:
        fingerprint["watch"] = discover_watch_targets(endpoint, result.json)
    return fingerprint
