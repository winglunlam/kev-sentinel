"""Deterministic rules: cheap, exact checks that run before Kev.

These are the checks a schema validator would run. They are necessary and not
sufficient: a payload can keep its shape while its meaning changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import EndpointConfig
from ..core.fetch import FetchResult
from ..core.fingerprint import compare_fingerprints, fingerprint
from ..core.store import Snapshot
from ..watch import compare_watch, discover_watch_targets


@dataclass(frozen=True)
class Finding:
    rule: str
    ok: bool
    severity: str  # "error" | "warn" | "info"
    message: str
    details: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return not self.ok and self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "ok": self.ok,
            "severity": self.severity,
            "message": self.message,
            "details": list(self.details),
        }


def looks_like_html(text: str) -> bool:
    head = text.lstrip()[:500].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<html" in head


def _schema_findings(endpoint: EndpointConfig, current_fp: dict[str, Any], baseline_fp: dict[str, Any]) -> list[Finding]:
    diff = compare_fingerprints(baseline_fp, current_fp)
    findings: list[Finding] = []
    breaking: list[str] = []
    for path in diff.missing:
        breaking.append(f"removed field: {path or '(root)'}")
    for path, base_type, cur_type in diff.retyped:
        breaking.append(f"type changed at {path or '(root)'}: {base_type} -> {cur_type}")
    for path in diff.nulled:
        breaking.append(f"field went null: {path}")
    if diff.records_path_changed is not None:
        base_path, cur_path = diff.records_path_changed
        breaking.append(f"record list moved: {base_path!r} -> {cur_path!r}")

    extra = list(diff.extra)
    if not endpoint.allow_extra_fields:
        breaking.extend(f"extra field: {path}" for path in extra)
        extra = []
    if breaking:
        findings.append(
            Finding("schema", False, "error", f"schema changed ({len(breaking)} issue(s))", tuple(breaking))
        )
    elif extra:
        findings.append(
            Finding("schema", True, "warn", f"schema ok; {len(extra)} extra field(s)", tuple(extra))
        )
    else:
        findings.append(Finding("schema", True, "info", "schema unchanged"))
    return findings


def _count_findings(
    endpoint: EndpointConfig, current_fp: dict[str, Any], baseline_fp: dict[str, Any]
) -> list[Finding]:
    current_count = current_fp.get("item_count")
    baseline_count = baseline_fp.get("item_count")
    details: list[str] = []
    failed = False

    if current_count is not None and baseline_count is not None:
        if current_count == 0:
            failed = True
            details.append(f"record list is empty (baseline {baseline_count})")
        elif baseline_count >= 4 and current_count < baseline_count * endpoint.count_floor:
            failed = True
            details.append(
                f"{current_count} items, down from {baseline_count} "
                f"(floor {endpoint.count_floor:.0%} of baseline)"
            )
        if endpoint.min_items is not None and current_count < endpoint.min_items:
            failed = True
            details.append(f"{current_count} items, below min_items={endpoint.min_items}")

    base_series = baseline_fp.get("series", {})
    cur_series = current_fp.get("series", {})
    for path, base_length in sorted(base_series.items()):
        if path not in cur_series:
            continue  # the schema rule already reports a removed path
        current_length = cur_series[path]
        if base_length >= 4 and current_length < base_length * endpoint.count_floor:
            failed = True
            details.append(f"series {path}: {current_length} values, down from {base_length}")

    if failed:
        return [Finding("count", False, "error", "record or series count regressed", tuple(details))]
    if current_count is None or baseline_count is None:
        return [
            Finding(
                "count",
                True,
                "info",
                f"{len(cur_series)} numeric series tracked" if cur_series else "item count not applicable",
            )
        ]
    return [Finding("count", True, "info", f"{current_count} items (baseline {baseline_count})")]


def _blank_ratio_findings(
    current_fp: dict[str, Any], baseline_fp: dict[str, Any]
) -> list[Finding]:
    cur = current_fp.get("stats", {}).get("blank_ratio", 0.0)
    base = baseline_fp.get("stats", {}).get("blank_ratio", 0.0)
    strings = current_fp.get("stats", {}).get("strings", 0)
    if strings >= 10 and base <= 0.05 and cur >= 0.4:
        return [
            Finding(
                "blank",
                False,
                "error",
                f"{cur:.0%} of string fields are blank (baseline {base:.0%})",
            )
        ]
    return [Finding("blank", True, "info", f"blank strings {cur:.0%} (baseline {base:.0%})")]


def _size_findings(current_fp: dict[str, Any], baseline_fp: dict[str, Any]) -> list[Finding]:
    cur = current_fp.get("stats", {}).get("bytes", 0)
    base = baseline_fp.get("stats", {}).get("bytes", 0)
    if base >= 500 and cur < base * 0.25:
        return [
            Finding(
                "size",
                False,
                "error",
                f"payload shrank to {cur} bytes from {base} ({cur / base:.0%})",
            )
        ]
    return [Finding("size", True, "info", f"{cur} bytes (baseline {base})")]


def _watch_findings(
    endpoint: EndpointConfig, result: FetchResult, baseline: Snapshot
) -> list[Finding]:
    watch = endpoint.watch
    if watch is None:
        return []
    if result.json is None:
        return [Finding("watch", True, "info", "watch skipped: response is not JSON")]
    recorded = baseline.fingerprint.get("watch")
    if not isinstance(recorded, dict):
        return [
            Finding(
                "watch",
                False,
                "error",
                "baseline has no watched values; re-run `sentinel baseline set`",
            )
        ]
    current = discover_watch_targets(endpoint, result.json)
    changes = compare_watch(recorded, current, watch)
    if changes:
        details = []
        for change in changes[:20]:
            if change.kind == "removed":
                details.append(f"{change.path}: removed (was {change.old!r})")
            else:
                details.append(f"{change.path}: {change.old!r} -> {change.new!r}")
        more = f" (+{len(changes) - 20} more)" if len(changes) > 20 else ""
        return [
            Finding(
                "watch",
                False,
                "error",
                f"{len(changes)} watched value(s) changed{more}",
                tuple(details),
            )
        ]
    return [Finding("watch", True, "info", f"{len(recorded)} watched values unchanged")]


def run_rules(endpoint: EndpointConfig, result: FetchResult, baseline: Snapshot) -> list[Finding]:
    """Run every deterministic rule and return one finding per rule."""
    if not result.ok:
        return [
            Finding(
                "transport",
                False,
                "error",
                f"fetch failed: {result.error or 'unknown error'}",
            )
        ]

    findings: list[Finding] = []
    findings.append(
        Finding(
            "transport",
            True,
            "info",
            f"HTTP {result.status} in {result.elapsed_ms} ms ({result.source})",
        )
    )

    status_ok = result.status == endpoint.expected_status
    findings.append(
        Finding(
            "status",
            status_ok,
            "error",
            f"HTTP {result.status} (expected {endpoint.expected_status})",
        )
    )

    html = looks_like_html(result.text)
    if endpoint.expect_json and html:
        findings.append(Finding("content-type", False, "error", "payload is an HTML page"))
    elif endpoint.expect_json and result.content_type and "json" not in result.content_type.lower():
        findings.append(
            Finding(
                "content-type",
                True,
                "warn",
                f"unexpected content type {result.content_type!r}",
            )
        )
    else:
        findings.append(Finding("content-type", True, "info", result.content_type or "unknown"))

    if not endpoint.expect_json:
        return findings

    if result.json is None:
        findings.append(
            Finding("json", False, "error", f"body is not valid JSON: {result.json_error}")
        )
        return findings
    findings.append(Finding("json", True, "info", "body parses as JSON"))

    current_fp = fingerprint(result.json, nbytes=result.size)
    baseline_fp = baseline.fingerprint
    findings.extend(_schema_findings(endpoint, current_fp, baseline_fp))
    findings.extend(_watch_findings(endpoint, result, baseline))
    findings.extend(_count_findings(endpoint, current_fp, baseline_fp))
    findings.extend(_blank_ratio_findings(current_fp, baseline_fp))
    findings.extend(_size_findings(current_fp, baseline_fp))
    return findings


def blocking_findings(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.blocking]


def summarize_rules(findings: list[Finding]) -> str:
    errors = [f for f in findings if f.blocking]
    if errors:
        return errors[0].message
    warnings = [f for f in findings if f.severity == "warn"]
    if warnings:
        return warnings[0].message
    schema = next((f for f in findings if f.rule == "schema"), None)
    return schema.message if schema else "ok"