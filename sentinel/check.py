"""One check: fetch, run rules against the baseline, ask Kev, record, alert."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .alert import maybe_alert
from .config import Config, EndpointConfig
from .core.fetch import FetchResult, fetch_endpoint
from .core.fingerprint import fingerprint_result
from .core.state import build_state
from .core.store import Store
from .errors import BaselineError, KevError
from .rules.deterministic import Finding, blocking_findings, looks_like_html, run_rules, summarize_rules
from .sem.client import Judge
from .sem.evaluate import SemResult, evaluate_answers


@dataclass(frozen=True)
class EndpointResult:
    endpoint: str
    ts: str
    ok: bool
    status: int | None
    source: str
    duration_ms: int
    findings: tuple[Finding, ...]
    semantic: tuple[SemResult, ...]
    semantic_error: str | None = None
    semantic_skipped: str | None = None
    semantic_latency_ms: float | None = None
    state_chars: int = 0
    size_bytes: int = 0
    alert_sent: bool = False

    @property
    def reasons(self) -> list[str]:
        reasons = [detail for finding in self.findings if finding.blocking for detail in (finding.details or (finding.message,))]
        reasons.extend(
            f"{result.qid}: {result.observed} fails `{result.fail_if}`"
            for result in self.semantic
            if not result.passed
        )
        if self.semantic_error:
            reasons.append(f"semantic check unavailable: {self.semantic_error}")
        return reasons

    @property
    def summary(self) -> str:
        rule_summary = summarize_rules(list(self.findings))
        if self.semantic_error:
            return f"{rule_summary}; semantic error"
        if self.semantic_skipped:
            return rule_summary
        failed = [result.qid for result in self.semantic if not result.passed]
        if failed:
            return f"{rule_summary}; semantic fail ({', '.join(failed)})"
        return rule_summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "ok": self.ok,
            "ts": self.ts,
            "status": self.status,
            "source": self.source,
            "duration_ms": self.duration_ms,
            "size_bytes": self.size_bytes,
            "state_chars": self.state_chars,
            "findings": [finding.to_dict() for finding in self.findings],
            "semantic": [result.to_dict() for result in self.semantic],
            "semantic_error": self.semantic_error,
            "semantic_skipped": self.semantic_skipped,
            "semantic_latency_ms": self.semantic_latency_ms,
            "reasons": self.reasons,
            "alert_sent": self.alert_sent,
        }


def run_check(
    config: Config,
    endpoint: EndpointConfig,
    store: Store,
    judge: Judge | None = None,
    *,
    live: bool = False,
    run_sem: bool = True,
    alerts: bool = True,
) -> EndpointResult:
    """Check one endpoint. Raises BaselineError when no baseline exists."""
    baseline = store.baseline(endpoint.name)
    if baseline is None:
        raise BaselineError(
            f"endpoint {endpoint.name!r} has no baseline; run "
            f"`sentinel baseline set {endpoint.name}` first"
        )
    previous = store.last_run(endpoint.name)
    started = time.perf_counter()
    result: FetchResult = fetch_endpoint(
        endpoint,
        timeout_s=endpoint.timeout_s or config.default_timeout_s,
        live=live,
    )
    findings = run_rules(endpoint, result, baseline)

    semantic: list[SemResult] = []
    semantic_error: str | None = None
    semantic_skipped: str | None = None
    sem_latency: float | None = None
    state_chars = 0
    sem_ran = False

    if not run_sem or not config.sem.enabled:
        semantic_skipped = "disabled"
    elif result.json is None:
        semantic_skipped = "response is not JSON"
    elif looks_like_html(result.text):
        semantic_skipped = "response is HTML"
    elif judge is None:
        semantic_error = "no Kev client configured"
    else:
        state = build_state(
            endpoint,
            result,
            sample_records=config.sample_records,
            max_chars=config.max_state_chars,
        )
        state_chars = len(state)
        try:
            response = judge.answer_many(state, list(endpoint.questions.values()))
        except KevError as exc:
            semantic_error = str(exc)
        else:
            semantic = evaluate_answers(endpoint, response)
            sem_latency = response.latency_ms
            sem_ran = True

    ok = (
        semantic_error is None
        and not blocking_findings(findings)
        and (not sem_ran or all(item.passed for item in semantic))
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    store.record_snapshot(endpoint.name, result, fingerprint_result(result))

    intermediate = EndpointResult(
        endpoint=endpoint.name,
        ts="",
        ok=ok,
        status=result.status,
        source=result.source,
        duration_ms=duration_ms,
        findings=tuple(findings),
        semantic=tuple(semantic),
        semantic_error=semantic_error,
        semantic_skipped=semantic_skipped,
        semantic_latency_ms=sem_latency,
        state_chars=state_chars,
        size_bytes=result.size,
    )
    summary = intermediate.to_dict()
    run_id = store.record_run(endpoint.name, ok, summary)

    alert_sent = False
    if alerts and not ok and semantic_error is None:
        result_with_alert = maybe_alert(
            config,
            endpoint,
            intermediate,
            previous_ok=None if previous is None else previous.ok,
            store=store,
        )
        if result_with_alert.sent:
            store.mark_alerted(run_id)
            alert_sent = True
            intermediate = EndpointResult(**{**intermediate.__dict__, "alert_sent": True})

    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return EndpointResult(
        endpoint=intermediate.endpoint,
        ts=ts,
        ok=intermediate.ok,
        status=intermediate.status,
        source=intermediate.source,
        duration_ms=intermediate.duration_ms,
        findings=intermediate.findings,
        semantic=intermediate.semantic,
        semantic_error=intermediate.semantic_error,
        semantic_skipped=intermediate.semantic_skipped,
        semantic_latency_ms=intermediate.semantic_latency_ms,
        state_chars=intermediate.state_chars,
        size_bytes=intermediate.size_bytes,
        alert_sent=alert_sent,
    )