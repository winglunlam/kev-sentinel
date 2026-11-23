"""Chaos injection: prove the detector catches changes, and learn which layer catches what.

Every mutator is a deterministic transformation of a real recorded payload. The
matrix it produces is the honest version of "this catches silent failures": some
mutations are caught by schema rules, some only by Kev, and the tool says which.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config, EndpointConfig
from .core.fetch import FetchResult, fetch_endpoint, synthetic_result
from .core.fingerprint import records_of
from .core.state import build_state
from .core.store import Store
from .errors import BaselineError, KevError
from .rules.deterministic import looks_like_html, run_rules
from .sem.client import Judge
from .sem.evaluate import SemResult, evaluate_answers

SPAM = (
    "Best deals on luxury watches, diet pills, and cheap holidays. Click now to claim "
    "your free trial and limited-time discount before it expires!"
)
DATE_RE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}")
CURRENCY_KEYS = ("price", "salary", "salar", "amount", "cost", "pay", "rate", "fee", "total")


@dataclass(frozen=True)
class Mutation:
    name: str
    description: str
    expect: str  # "rules" | "semantics" | "either": which layer should catch it
    status: int
    content_type: str
    body: bytes


@dataclass(frozen=True)
class Mutator:
    name: str
    description: str
    expect: str
    apply: Callable[[FetchResult, dict[str, Any], str], "Mutation"]


def _require_json(base: FetchResult, name: str) -> Any:
    if base.json is None:
        raise ValueError(f"chaos mutation {name!r} needs a JSON body; this endpoint returned {base.json_error}")
    return copy.deepcopy(base.json)


def _spam_like(original: str) -> str:
    if not original:
        return SPAM
    repeats = (len(original) // len(SPAM)) + 1
    return (SPAM * repeats)[: len(original)]


def _is_entity_string(value: str) -> bool:
    if len(value) < 12 or value.startswith(("http://", "https://")):
        return False
    return DATE_RE.search(value) is None


def _replace_records(data: Any, new_records: Any) -> Any:
    if isinstance(data, list):
        return new_records
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value and any(isinstance(item, dict) for item in value):
                data[key] = new_records
                return data
    return data


def _mutate_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        return {key: _mutate_strings(child, fn) for key, child in value.items()}
    if isinstance(value, list):
        return [_mutate_strings(child, fn) for child in value]
    if isinstance(value, str):
        return fn(value)
    return value


def _make(spec: Mutator, data: Any) -> Mutation:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Mutation(spec.name, spec.description, spec.expect, 200, "application/json", body)


# -- mutators ---------------------------------------------------------------


def _wrong_entity(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    data = _require_json(base, name)
    changed = 0
    for item in records_of(data):
        if not isinstance(item, dict):
            continue
        for key, value in list(item.items()):
            if isinstance(value, str) and _is_entity_string(value):
                item[key] = _spam_like(value)
                changed += 1
    if changed == 0:
        for item in records_of(data):
            if isinstance(item, dict):
                for key, value in list(item.items()):
                    if isinstance(value, str):
                        item[key] = _spam_like(value)
    return _make(spec, data)


def _stale_dates(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    data = _require_json(base, name)

    def shift(value: str) -> str:
        return DATE_RE.sub(lambda m: f"2019{m.group(0)[4:]}", value)

    return _make(spec, _mutate_strings(data, shift))


def _currency_swap(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    """Switch currency codes and symbols while keeping the payload's shape."""
    spec = _REGISTRY[name]
    data = _require_json(base, name)
    replacements = {"USD": "EUR", "usd": "eur", "GBP": "EUR", "gbp": "eur"}
    symbols = str.maketrans({"$": "€", "£": "€", "¥": "€"})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            if node.strip() in replacements:
                return replacements[node.strip()]
            return node.translate(symbols)
        return node

    return _make(spec, walk(data))


def _unit_swap(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    """Convert Celsius values to Fahrenheit but leave the stated unit alone."""
    spec = _REGISTRY[name]
    data = _require_json(base, name)

    def walk(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item, key) for item in node]
        if isinstance(node, (int, float)) and not isinstance(node, bool) and key and "temp" in key.lower():
            return round(node * 9 / 5 + 32, 1)
        return node

    return _make(spec, walk(data))


def _status_flip(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    """Flip health words to failure states and stretch latencies."""
    spec = _REGISTRY[name]
    data = _require_json(base, name)
    failures = {"ok": "outage", "operational": "down", "degraded": "down", "healthy": "unhealthy"}

    def walk(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item, key) for item in node]
        if isinstance(node, str) and node.strip().lower() in failures:
            return failures[node.strip().lower()]
        if isinstance(node, (int, float)) and not isinstance(node, bool) and key and key.lower().endswith("_ms"):
            return round(node * 20, 1)
        return node

    return _make(spec, walk(data))


def _field_rename(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    data = _require_json(base, name)
    rename: dict[str, str] = dict(opts.get("rename") or {})
    if not rename:
        counts: Counter[str] = Counter()
        for item in records_of(data):
            if isinstance(item, dict):
                counts.update(key for key, value in item.items() if isinstance(value, str))
        if counts:
            key = counts.most_common(1)[0][0]
            rename = {key: f"{key}_v2"}
    if not rename:
        raise ValueError("field-rename found no string field to rename; pass --rename old=new")
    for item in records_of(data):
        if not isinstance(item, dict):
            continue
        for old, new in rename.items():
            if old in item:
                item[new] = item.pop(old)
    return _make(spec, data)


def _null_fields(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    data = _require_json(base, name)
    counter = {"n": 0}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, (str, int, float)) and not isinstance(node, bool):
            counter["n"] += 1
            if counter["n"] % 2 == 0:
                return None
        return node

    return _make(spec, walk(data))


def _empty_list(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    data = _require_json(base, name)
    return _make(spec, _replace_records(data, []))


def _string_numbers(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    data = _require_json(base, name)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            return str(node)
        return node

    return _make(spec, walk(data))


def _status_500(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    body = json.dumps({"error": "internal server error"}).encode()
    return Mutation(spec.name, spec.description, spec.expect, 500, "application/json", body)


def _html_error(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    body = (
        "<!doctype html><html><head><title>Access denied</title></head><body>"
        "<h1>Access denied</h1><p>Your request looked automated. Enable JavaScript and "
        "complete the captcha to continue.</p></body></html>"
    ).encode()
    return Mutation(spec.name, spec.description, spec.expect, 200, "text/html; charset=utf-8", body)


def _stream_silence(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    """Drop content events, leaving only connection/lifecycle events."""
    spec = _REGISTRY[name]
    data = _require_json(base, name)
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ValueError("stream-silence only applies to event-stream payloads")
    keep = [
        event
        for event in data["events"]
        if isinstance(event, dict) and event.get("event") in {"connect", "ping", "message_stop"}
    ]
    if not keep:
        raise ValueError("stream-silence needs at least one lifecycle event to keep")
    return _make(spec, {**{key: value for key, value in data.items() if key != "events"}, "events": keep})


def _truncated(base: FetchResult, opts: dict[str, Any], name: str) -> Mutation:
    spec = _REGISTRY[name]
    body = base.body[: max(1, int(len(base.body) * 0.4))]
    return Mutation(spec.name, spec.description, spec.expect, 200, "application/json", body)


def _build_registry() -> dict[str, Mutator]:
    rows = [
        ("wrong-entity", "replace record text with unrelated content, same shape and size", "semantics", _wrong_entity),
        ("stale-dates", "shift every ISO date to 2019", "semantics", _stale_dates),
        ("currency-swap", "switch currency codes and symbols, same shape", "semantics", _currency_swap),
        ("unit-swap", "convert Celsius values to Fahrenheit, leave the unit label alone", "semantics", _unit_swap),
        ("status-flip", "flip health words to failure states and stretch latencies", "semantics", _status_flip),
        ("stream-silence", "drop content events, leaving only connection/lifecycle events", "semantics", _stream_silence),
        ("field-rename", "rename the most common string field", "rules", _field_rename),
        ("null-fields", "null every other scalar field", "rules", _null_fields),
        ("empty-list", "keep the schema, empty the record list", "rules", _empty_list),
        ("string-numbers", "turn numbers into strings", "rules", _string_numbers),
        ("status-500", "return HTTP 500", "rules", _status_500),
        ("html-error", "return an HTML captcha page with HTTP 200", "rules", _html_error),
        ("truncated", "cut the body to 40%", "rules", _truncated),
    ]
    return {name: Mutator(name, description, expect, apply) for name, description, expect, apply in rows}


_REGISTRY = _build_registry()
MUTATIONS: tuple[str, ...] = tuple(_REGISTRY)
MUTATORS = _REGISTRY


def mutation_names() -> tuple[str, ...]:
    return MUTATIONS


@dataclass(frozen=True)
class ChaosResult:
    endpoint: str
    mutation: str
    description: str
    expected: str
    rules_caught: bool
    rules_reasons: tuple[str, ...]
    sem_caught: bool
    sem_reasons: tuple[str, ...]
    sem_error: str | None
    sem_skipped: str | None
    detected: bool
    applicable: bool = True

    @property
    def passed(self) -> bool:
        return self.detected

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "mutation": self.mutation,
            "expected": self.expected,
            "rules_caught": self.rules_caught,
            "rules_reasons": list(self.rules_reasons),
            "semantic_caught": self.sem_caught,
            "semantic_reasons": list(self.sem_reasons),
            "semantic_error": self.sem_error,
            "semantic_skipped": self.sem_skipped,
            "detected": self.detected,
            "applicable": self.applicable,
        }


def _changes_payload(mutation: "Mutation", base: FetchResult, mutated: FetchResult) -> bool:
    if mutation.status != base.status or mutation.content_type != base.content_type:
        return True
    if mutated.json is None or base.json is None:
        return True
    return mutated.json != base.json


def run_chaos(
    config: Config,
    endpoint: EndpointConfig,
    store: Store,
    judge: Judge | None,
    *,
    names: list[str] | None = None,
    live: bool = False,
    rename: dict[str, str] | None = None,
) -> list[ChaosResult]:
    baseline = store.baseline(endpoint.name)
    if baseline is None:
        raise BaselineError(
            f"endpoint {endpoint.name!r} has no baseline; run `sentinel baseline set {endpoint.name}` first"
        )
    selected = names or list(MUTATIONS)
    unknown = [name for name in selected if name not in _REGISTRY]
    if unknown:
        raise ValueError(f"unknown chaos mutation(s) {unknown}; available: {list(MUTATIONS)}")

    base = fetch_endpoint(
        endpoint,
        timeout_s=endpoint.timeout_s or config.default_timeout_s,
        live=live,
    )
    if not base.ok:
        raise ValueError(f"cannot fetch {endpoint.name}: {base.error}")

    results: list[ChaosResult] = []
    for name in selected:
        mutator = _REGISTRY[name]
        try:
            mutation = mutator.apply(base, {"rename": rename or {}}, name)
        except ValueError:
            results.append(
                ChaosResult(
                    endpoint=endpoint.name,
                    mutation=name,
                    description=mutator.description,
                    expected=mutator.expect,
                    rules_caught=False,
                    rules_reasons=(),
                    sem_caught=False,
                    sem_reasons=(),
                    sem_error=None,
                    sem_skipped="injection does not apply to this payload",
                    detected=False,
                    applicable=False,
                )
            )
            continue
        mutated = synthetic_result(
            endpoint.name, body=mutation.body, status=mutation.status, content_type=mutation.content_type
        )
        applicable = _changes_payload(mutation, base, mutated)
        if not applicable:
            results.append(
                ChaosResult(
                    endpoint=endpoint.name,
                    mutation=name,
                    description=mutator.description,
                    expected=mutator.expect,
                    rules_caught=False,
                    rules_reasons=(),
                    sem_caught=False,
                    sem_reasons=(),
                    sem_error=None,
                    sem_skipped="injection does not apply to this payload",
                    detected=False,
                    applicable=False,
                )
            )
            continue
        findings = run_rules(endpoint, mutated, baseline)
        rule_reasons = tuple(
            detail
            for finding in findings
            if finding.blocking
            for detail in (finding.details or (finding.message,))
        )
        semantic: list[SemResult] = []
        sem_error: str | None = None
        sem_skipped: str | None = None
        if mutated.json is None:
            sem_skipped = "response is not JSON"
        elif looks_like_html(mutated.text):
            sem_skipped = "response is HTML"
        elif judge is None:
            sem_skipped = "disabled (--no-sem)"
        else:
            state = build_state(
                endpoint, mutated, sample_records=config.sample_records, max_chars=config.max_state_chars
            )
            try:
                response = judge.answer_many(state, list(endpoint.questions.values()))
            except KevError as exc:
                sem_error = str(exc)
            else:
                semantic = evaluate_answers(endpoint, response)
        sem_reasons = tuple(
            f"{item.qid}: {item.observed} fails `{item.fail_if}`" for item in semantic if not item.passed
        )
        rules_caught = bool(rule_reasons)
        sem_caught = bool(sem_reasons)
        results.append(
            ChaosResult(
                endpoint=endpoint.name,
                mutation=name,
                description=mutator.description,
                expected=mutator.expect,
                rules_caught=rules_caught,
                rules_reasons=rule_reasons,
                sem_caught=sem_caught,
                sem_reasons=sem_reasons,
                sem_error=sem_error,
                sem_skipped=sem_skipped,
                detected=rules_caught or sem_caught,
            )
        )
    return results