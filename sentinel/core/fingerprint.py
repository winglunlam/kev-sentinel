"""Structural fingerprint of a JSON payload.

The fingerprint is what ``sentinel init`` records as the baseline. It is
deliberately structural: paths and types, not values, so a renamed field or a
dropped field is visible but a price change is not. Meaning-level checks are
Kev's job.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .fetch import FetchResult

MAX_ITEMS_SCANNED = 50
FINGERPRINT_VERSION = 1


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _join(base: str, key: str) -> str:
    return f"{base}.{key}" if base else key


def find_records(value: Any) -> tuple[str | None, list[Any] | None]:
    """Locate the list of records a payload is mostly about.

    Root arrays are the common case (most JSON APIs). Otherwise the first
    top-level list whose items are objects wins (``{"results": [...]}``).
    Returns ``(paths_prefix, records)``; the prefix is ``""`` for a root array.
    """
    if isinstance(value, list):
        return "", value
    if isinstance(value, dict):
        candidates = [(str(key), child) for key, child in value.items() if isinstance(child, list)]
        for key, child in candidates:
            if child and any(isinstance(item, dict) for item in child):
                return key, child
        if candidates:
            key, child = max(candidates, key=lambda pair: len(pair[1]))
            return key, child
    return None, None


def fingerprint(value: Any, *, nbytes: int | None = None) -> dict[str, Any]:
    """Build a JSON-serialisable structural fingerprint."""
    paths: dict[str, set[str]] = {}
    nulls: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    stats = {"leaves": 0, "strings": 0, "blank_strings": 0}
    series: dict[str, int] = {}

    def walk(node: Any, path: str) -> None:
        kind = type_name(node)
        paths.setdefault(path, set()).add(kind)
        totals[path] += 1
        if node is None:
            nulls[path] += 1
            return
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, _join(path, str(key)))
            return
        if isinstance(node, list):
            if len(node) >= 4 and len(series) < 25:
                sample = node[:MAX_ITEMS_SCANNED]
                if all(
                    isinstance(item, (int, float, str)) and not isinstance(item, bool)
                    for item in sample
                ):
                    series.setdefault(path, len(node))
            for child in node[:MAX_ITEMS_SCANNED]:
                walk(child, f"{path}[]")
            return
        stats["leaves"] += 1
        if isinstance(node, str):
            stats["strings"] += 1
            if not node.strip():
                stats["blank_strings"] += 1

    walk(value, "")
    records_path, records = find_records(value)
    strings = stats["strings"]
    blank_ratio = (stats["blank_strings"] / strings) if strings else 0.0
    if nbytes is None:
        import json

        nbytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    return {
        "version": FINGERPRINT_VERSION,
        "root": type_name(value),
        "records_path": records_path,
        "item_count": len(records) if records is not None else None,
        "paths": {path: sorted(kinds) for path, kinds in sorted(paths.items())},
        "nulls": {path: [nulls[path], totals[path]] for path in sorted(paths) if nulls[path]},
        "series": dict(sorted(series.items())),
        "stats": {
            "bytes": int(nbytes),
            "leaves": stats["leaves"],
            "strings": strings,
            "blank_ratio": round(blank_ratio, 4),
        },
    }


def _display_type(kinds: list[str] | set[str]) -> str:
    unique = set(kinds)
    if unique == {"null"}:
        return "null"
    unique.discard("null")
    if not unique:
        return "null"
    if len(unique) == 1:
        return next(iter(unique))
    return "mixed"


@dataclass(frozen=True)
class SchemaDiff:
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    retyped: tuple[tuple[str, str, str], ...] = ()  # (path, baseline, current)
    nulled: tuple[str, ...] = ()
    records_path_changed: tuple[str | None, str | None] | None = None
    item_count: tuple[int | None, int | None] | None = None

    @property
    def breaking(self) -> bool:
        return bool(self.missing or self.retyped or self.nulled or self.records_path_changed)


def compare_fingerprints(baseline: dict[str, Any], current: dict[str, Any]) -> SchemaDiff:
    """Compare two fingerprints. Extra fields are always reported; the caller decides severity."""
    base_paths = baseline.get("paths", {})
    cur_paths = current.get("paths", {})
    base_keys, cur_keys = set(base_paths), set(cur_paths)

    missing = tuple(sorted(base_keys - cur_keys))
    extra = tuple(sorted(cur_keys - base_keys))

    retyped = []
    nulled = []
    base_nulls = baseline.get("nulls", {})
    cur_nulls = current.get("nulls", {})
    for path in sorted(base_keys & cur_keys):
        base_type = _display_type(base_paths[path])
        cur_type = _display_type(cur_paths[path])
        if base_type == cur_type:
            continue
        if cur_type == "null":
            nulled.append(path)
            continue
        if base_type == "mixed" or cur_type == "mixed":
            retyped.append((path, base_type, cur_type))
            continue
        retyped.append((path, base_type, cur_type))

    for path, (cur_null, cur_total) in cur_nulls.items():
        if path not in base_paths or cur_total == 0:
            continue
        base_null, base_total = base_nulls.get(path, [0, cur_total])
        base_ratio = base_null / base_total if base_total else 0.0
        cur_ratio = cur_null / cur_total
        if base_ratio <= 0.05 and cur_ratio >= 0.5 and cur_total >= 3 and path not in nulled:
            nulled.append(path)

    records_path_changed = None
    if baseline.get("records_path") != current.get("records_path"):
        records_path_changed = (baseline.get("records_path"), current.get("records_path"))

    item_count = None
    if baseline.get("item_count") is not None or current.get("item_count") is not None:
        item_count = (baseline.get("item_count"), current.get("item_count"))

    return SchemaDiff(
        missing=missing,
        extra=extra,
        retyped=tuple(retyped),
        nulled=tuple(sorted(set(nulled))),
        records_path_changed=records_path_changed,
        item_count=item_count,
    )


def records_of(value: Any) -> list[Any]:
    """The record list of a payload, or an empty list."""
    _, records = find_records(value)
    return records or []


def body_bytes(value: Any) -> int:
    import json

    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def fingerprint_result(result: "FetchResult") -> dict[str, Any]:
    """Fingerprint a fetched response; non-JSON bodies get a structural placeholder."""
    if result.json is not None:
        return fingerprint(result.json, nbytes=result.size)
    return {
        "version": FINGERPRINT_VERSION,
        "root": "non-json",
        "records_path": None,
        "item_count": None,
        "paths": {},
        "nulls": {},
        "stats": {"bytes": result.size, "leaves": 0, "strings": 0, "blank_ratio": 0.0},
    }