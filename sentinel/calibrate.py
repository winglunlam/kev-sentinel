"""Calibration: measure each question on the clean payload and on injected drift,
then suggest a threshold with margin on both sides.

The point is that a threshold is not a taste. It is the midpoint between what the
clean payload scores and what the worst injected drift scores, checked against
your checkpoint. `sentinel calibrate` prints it; a human commits it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .chaos import MUTATORS
from .config import Config, EndpointConfig, Question
from .core.fetch import fetch_endpoint, synthetic_result
from .core.state import build_state
from .core.store import Store
from .errors import BaselineError, KevError
from .sem.client import Judge
from .sem.types import Answer

WEAK_MARGIN = 0.15


@dataclass(frozen=True)
class CalibrationShot:
    mutation: str
    value: float | str
    observed: str


@dataclass(frozen=True)
class CalibrationRow:
    endpoint: str
    question: str
    qtype: str
    clean_value: float | str
    clean_observed: str
    shots: tuple[CalibrationShot, ...]
    suggested_fail_if: str | None
    separation: float | None
    note: str
    worst_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "question": self.question,
            "type": self.qtype,
            "clean": self.clean_observed,
            "injections": [
                {"mutation": shot.mutation, "observed": shot.observed} for shot in self.shots
            ],
            "suggested_fail_if": self.suggested_fail_if,
            "separation": self.separation,
            "note": self.note,
        }


def _round_step(value: float, step: float = 0.05) -> float:
    return round(round(value / step) * step, 2)


def _suggest(
    question: Question, clean: Answer, shots: list[tuple[str, Answer]]
) -> tuple[str | None, float | None, str, str]:
    """Return (suggested_fail_if, separation, note, worst_label)."""
    if question.type == "noul":
        clean_p = float(clean.value)
        values = [(float(answer.value), name) for name, answer in shots]
        if not values:
            return None, None, "no injection ran", ""
        up_value, up_name = max(values)
        down_value, down_name = min(values)
        up_delta = up_value - clean_p
        down_delta = clean_p - down_value
        if max(up_delta, down_delta) < 0.10:
            return (
                None,
                max(up_delta, down_delta),
                "no separation: clean and injected payloads score within 0.10",
                "",
            )
        if up_delta >= down_delta:
            threshold = _round_step((clean_p + up_value) / 2)
            threshold = max(threshold, 0.05)
            if threshold <= clean_p:
                threshold = _round_step(clean_p + 0.05)
            return (
                f"p > {threshold:g}",
                up_delta,
                f"clean {clean_p:.2f}, {up_name} {up_value:.2f}",
                f"{up_name} {up_value:.2f}",
            )
        threshold = _round_step((clean_p + down_value) / 2)
        threshold = min(threshold, 0.95)
        if threshold >= clean_p:
            threshold = _round_step(clean_p - 0.05)
        return (
            f"p < {threshold:g}",
            down_delta,
            f"clean {clean_p:.2f}, {down_name} {down_value:.2f}",
            f"{down_name} {down_value:.2f}",
        )

    if question.type == "score":
        clean_score = float(clean.value)
        values = [(float(answer.value), name) for name, answer in shots]
        if not values:
            return None, None, "no injection ran", ""
        up_value, up_name = max(values)
        down_value, down_name = min(values)
        up_delta = up_value - clean_score
        down_delta = clean_score - down_value
        if max(up_delta, down_delta) < 0.5:
            return (
                None,
                max(up_delta, down_delta),
                "no separation: clean and injected scores are within 0.5",
                "",
            )
        if up_delta >= down_delta:
            threshold = _round_step((clean_score + up_value) / 2, 0.1)
            return (
                f"score > {threshold:g}",
                up_delta,
                f"clean {clean_score:.2f}, {up_name} {up_value:.2f}",
                f"{up_name} {up_value:.2f}",
            )
        threshold = _round_step((clean_score + down_value) / 2, 0.1)
        return (
            f"score < {threshold:g}",
            down_delta,
            f"clean {clean_score:.2f}, {down_name} {down_value:.2f}",
            f"{down_name} {down_value:.2f}",
        )

    flips = [(name, answer) for name, answer in shots if answer.value != clean.value]
    if flips:
        name, answer = flips[0]
        return (
            f"choice != '{clean.value}'",
            1.0,
            f"clean {clean.value}, {name} flips to {answer.value}",
            f"{name} {answer.value}",
        )
    return None, None, "no injection changed the chosen option", ""


def run_calibration(
    config: Config,
    endpoint: EndpointConfig,
    store: Store,
    judge: Judge,
    *,
    live: bool = False,
    mutation_names: list[str] | None = None,
) -> list[CalibrationRow]:
    baseline = store.baseline(endpoint.name)
    if baseline is None:
        raise BaselineError(
            f"endpoint {endpoint.name!r} has no baseline; run `sentinel baseline set {endpoint.name}` first"
        )
    default_injections = [name for name, mutator in MUTATORS.items() if mutator.expect == "semantics"]
    selected = mutation_names or default_injections
    base = fetch_endpoint(
        endpoint,
        timeout_s=endpoint.timeout_s or config.default_timeout_s,
        live=live,
    )
    if not base.ok:
        raise KevError(f"cannot fetch {endpoint.name}: {base.error}")
    if base.json is None:
        raise KevError(f"{endpoint.name} is not JSON, so semantic calibration does not apply")
    if not endpoint.questions:
        return []

    clean_state = build_state(
        endpoint, base, sample_records=config.sample_records, max_chars=config.max_state_chars
    )
    clean_response = judge.answer_many(clean_state, list(endpoint.questions.values()))
    store.record_snapshot(endpoint.name, base, _fingerprint(base))

    shots: dict[str, list[tuple[str, Answer]]] = {qid: [] for qid in endpoint.questions}
    for name in selected:
        mutator = MUTATORS[name]
        try:
            mutation = mutator.apply(base, {}, name)
        except ValueError:
            continue
        mutated = synthetic_result(
            endpoint.name, body=mutation.body, status=mutation.status, content_type=mutation.content_type
        )
        if mutated.json is None:
            continue
        state = build_state(
            endpoint, mutated, sample_records=config.sample_records, max_chars=config.max_state_chars
        )
        try:
            response = judge.answer_many(state, list(endpoint.questions.values()))
        except KevError:
            continue
        for qid, answer in response.answers.items():
            shots[qid].append((name, answer))

    rows: list[CalibrationRow] = []
    for qid, question in endpoint.questions.items():
        clean = clean_response.answers[qid]
        question_shots = shots[qid]
        suggested, separation, note, worst_label = _suggest(question, clean, question_shots)
        rows.append(
            CalibrationRow(
                endpoint=endpoint.name,
                question=qid,
                qtype=question.type,
                clean_value=clean.value,
                clean_observed=clean.describe(),
                shots=tuple(
                    CalibrationShot(
                        mutation=name,
                        value=answer.value,
                        observed=answer.describe(),
                    )
                    for name, answer in question_shots
                ),
                suggested_fail_if=suggested,
                separation=separation,
                note=note,
                worst_label=worst_label,
            )
        )
    return rows


def _fingerprint(result):
    from .core.fingerprint import fingerprint_result

    return fingerprint_result(result)