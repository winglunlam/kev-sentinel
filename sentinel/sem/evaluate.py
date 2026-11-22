"""Turn Kev answers into pass/fail results using the endpoint's declared conditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import EndpointConfig
from ..errors import KevError
from .client import JudgeResponse
from .types import Answer


@dataclass(frozen=True)
class SemResult:
    qid: str
    qtype: str
    fail_if: str
    passed: bool
    observed: str
    answer: Answer

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.qid,
            "type": self.qtype,
            "fail_if": self.fail_if,
            "passed": self.passed,
            "observed": self.observed,
        }


def evaluate_answers(endpoint: EndpointConfig, response: JudgeResponse) -> list[SemResult]:
    results: list[SemResult] = []
    for qid, question in endpoint.questions.items():
        answer = response.answers.get(qid)
        if answer is None:
            raise KevError(f"Kev did not return an answer for question {qid!r}")
        passed, observed = question.fail_if.check(answer)
        results.append(
            SemResult(
                qid=qid,
                qtype=question.type,
                fail_if=question.fail_if.raw,
                passed=passed,
                observed=observed,
                answer=answer,
            )
        )
    return results