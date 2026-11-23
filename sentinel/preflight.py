"""Decide whether a config is safe to schedule, not just whether it runs.

`check` says "this run passed". `preflight` asks the harder question: is the
baseline real, does the clean payload still pass, did every question separate
clean from injected drift with enough margin, and did the chaos injections get
caught by some layer? It is the gate to run once before trusting a config to a
cron job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .calibrate import WEAK_MARGIN, CalibrationRow, run_calibration
from .chaos import ChaosResult, run_chaos
from .check import EndpointResult, run_check
from .config import Config, EndpointConfig
from .core.store import Store

@dataclass(frozen=True)
class EndpointPreflight:
    endpoint: str
    baseline: bool
    clean: bool | None
    weak_questions: tuple[tuple[str, float], ...] = ()
    unproven_questions: tuple[str, ...] = ()
    proven_questions: tuple[str, ...] = ()
    semantic_error: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "baseline": self.baseline,
            "clean": self.clean,
            "weak_questions": [
                {"question": qid, "margin": margin} for qid, margin in self.weak_questions
            ],
            "unproven_questions": list(self.unproven_questions),
            "proven_questions": list(self.proven_questions),
            "semantic_error": self.semantic_error,
            "note": self.note,
        }


@dataclass(frozen=True)
class PreflightReport:
    endpoints: tuple[EndpointPreflight, ...]
    missed_rules: tuple[str, ...] = ()
    missed_semantics: tuple[str, ...] = ()
    kev_unavailable: bool = False
    calibration_ran: bool = False
    extra: tuple[str, ...] = field(default_factory=tuple)

    @property
    def proven_questions(self) -> int:
        return sum(len(item.proven_questions) for item in self.endpoints)

    @property
    def any_questions(self) -> bool:
        return any(item.proven_questions or item.weak_questions or item.unproven_questions for item in self.endpoints)

    @property
    def go(self) -> bool:
        if self.kev_unavailable:
            return False
        if not self.endpoints or any(not item.baseline or item.clean is not True for item in self.endpoints):
            return False
        if self.missed_rules or self.missed_semantics:
            return False
        if self.calibration_ran and self.any_questions and self.proven_questions == 0:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "go": self.go,
            "endpoints": [item.to_dict() for item in self.endpoints],
            "missed_rules": list(self.missed_rules),
            "missed_semantics": list(self.missed_semantics),
            "kev_unavailable": self.kev_unavailable,
            "calibration_ran": self.calibration_ran,
            "proven_questions": self.proven_questions,
            "extra": list(self.extra),
        }


def _classify_calibration(
    rows: Sequence[CalibrationRow],
) -> tuple[tuple[tuple[str, float], ...], tuple[str, ...], tuple[str, ...]]:
    weak = []
    unproven = []
    proven = []
    for row in rows:
        if row.suggested_fail_if is None:
            # No threshold could be measured: either no injection touched the
            # answer (a guard question) or clean and drift were too close.
            unproven.append(row.question)
            continue
        if row.separation is not None and row.separation < WEAK_MARGIN:
            weak.append((row.question, float(row.separation)))
        else:
            proven.append(row.question)
    return tuple(weak), tuple(unproven), tuple(proven)


def _coverage(results: Sequence[ChaosResult], *, sem_enabled: bool) -> tuple[set[str], set[str]]:
    """Return mutation classes undetected across all endpoints."""
    tested: set[str] = set()
    missed_rules: set[str] = set()
    missed_semantics: set[str] = set()
    for result in results:
        if not result.applicable:
            continue
        if result.expected == "semantics" and not sem_enabled:
            continue
        tested.add(result.mutation)
        if result.detected:
            continue
        if result.expected == "rules":
            missed_rules.add(result.mutation)
        else:
            missed_semantics.add(result.mutation)
    # A class counts as missed only if every applicable result missed it.
    for name in list(missed_rules):
        if any(r.detected for r in results if r.mutation == name and r.applicable):
            missed_rules.discard(name)
    for name in list(missed_semantics):
        if any(r.detected for r in results if r.mutation == name and r.applicable):
            missed_semantics.discard(name)
    return missed_rules, missed_semantics


def run_preflight(
    config: Config,
    endpoints: Sequence[EndpointConfig],
    store: Store,
    judge,
    *,
    live: bool = False,
    semantic: bool = True,
    calibrate: bool = True,
) -> PreflightReport:
    rows: list[EndpointPreflight] = []
    all_chaos: list[ChaosResult] = []
    kev_unavailable = False

    for endpoint in endpoints:
        baseline = store.baseline(endpoint.name)
        if baseline is None:
            rows.append(EndpointPreflight(endpoint.name, baseline=False, clean=None, note="no baseline"))
            continue
        result: EndpointResult = run_check(
            config,
            endpoint,
            store,
            judge,
            live=live,
            run_sem=semantic,
            alerts=False,
        )
        weak: tuple[tuple[str, float], ...] = ()
        unproven: tuple[str, ...] = ()
        proven: tuple[str, ...] = ()
        if semantic and calibrate and endpoint.questions:
            calibration = run_calibration(config, endpoint, store, judge, live=live)
            weak, unproven, proven = _classify_calibration(calibration)
        if result.semantic_error:
            kev_unavailable = True
        chaos: list[ChaosResult] = run_chaos(
            config, endpoint, store, judge if semantic else None, live=live
        )
        all_chaos.extend(chaos)
        rows.append(
            EndpointPreflight(
                endpoint=endpoint.name,
                baseline=True,
                clean=result.ok,
                weak_questions=weak,
                unproven_questions=unproven,
                proven_questions=proven,
                semantic_error=result.semantic_error,
            )
        )

    missed_rules, missed_semantics = _coverage(all_chaos, sem_enabled=semantic)
    extra = []
    if not semantic:
        extra.append("semantic layer skipped (--no-sem)")
    if not calibrate:
        extra.append("calibration skipped (--no-calibrate)")
    return PreflightReport(
        endpoints=tuple(rows),
        missed_rules=tuple(sorted(missed_rules)),
        missed_semantics=tuple(sorted(missed_semantics)),
        kev_unavailable=kev_unavailable,
        calibration_ran=calibrate and semantic,
        extra=tuple(extra),
    )
