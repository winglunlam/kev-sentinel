"""Kev client: one batched System One request per endpoint.

Cost and latency are the argument for this tool, so the client does the least
possible: all questions for an endpoint go in one request, the answers come back
as probabilities, and there is no text generation anywhere.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, Sequence

import httpx

from ..config import Question, SemConfig
from ..errors import KevError
from .types import Answer


@dataclass(frozen=True)
class JudgeResponse:
    answers: dict[str, Answer]
    latency_ms: float
    model: str | None = None
    usage: dict | None = None


class Judge(Protocol):
    def answer_many(self, state: str, questions: Sequence[Question]) -> JudgeResponse: ...


def _parse_answer(qid: str, question: Question, raw: object) -> Answer:
    if not isinstance(raw, dict):
        raise KevError(f"Kev returned no answer object for question {qid!r}")
    if question.type == "noul":
        value = raw.get("noul")
        if not isinstance(value, (int, float)):
            raise KevError(f"Kev answer for {qid!r} has no numeric `noul`: {raw!r}")
        return Answer(qid=qid, qtype="noul", value=float(value))
    if question.type == "choice":
        choice = raw.get("choice")
        if not isinstance(choice, str):
            raise KevError(f"Kev answer for {qid!r} has no `choice`: {raw!r}")
        probabilities = raw.get("probabilities")
        probabilities = (
            {str(k): float(v) for k, v in probabilities.items()}
            if isinstance(probabilities, dict) and all(isinstance(v, (int, float)) for v in probabilities.values())
            else None
        )
        confidence = raw.get("confidence")
        return Answer(
            qid=qid,
            qtype="choice",
            value=choice,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            probabilities=probabilities,
        )
    score = raw.get("score")
    if not isinstance(score, (int, float)):
        raise KevError(f"Kev answer for {qid!r} has no numeric `score`: {raw!r}")
    confidence = raw.get("confidence")
    return Answer(
        qid=qid,
        qtype="score",
        value=float(score),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
    )


def answers_from_response(data: dict, questions: Sequence[Question]) -> dict[str, Answer]:
    """Parse a System One response into ``Answer`` objects."""
    raw_answers = data.get("answers")
    if not isinstance(raw_answers, dict):
        raise KevError("Kev response has no `answers` object")
    answers: dict[str, Answer] = {}
    for question in questions:
        if question.qid not in raw_answers:
            raise KevError(f"Kev did not answer question {question.qid!r}")
        answers[question.qid] = _parse_answer(question.qid, question, raw_answers[question.qid])
    return answers


class KevClient:
    """HTTP client for a local ``python -m kev.serve`` process."""

    def __init__(
        self,
        sem: SemConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 2,
    ):
        self.sem = sem
        self._transport = transport
        self._max_attempts = max(1, max_attempts)

    @property
    def url(self) -> str:
        return f"{self.sem.base_url}/v1/systemone"

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.sem.api_key:
            headers["authorization"] = f"Bearer {self.sem.api_key}"
        return headers

    def _post(self, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                with httpx.Client(
                    base_url=self.sem.base_url,
                    timeout=self.sem.timeout_s,
                    transport=self._transport,
                    headers=self._headers(),
                ) as client:
                    response = client.post("/v1/systemone", json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise KevError(
                            f"Kev at {self.sem.base_url} returned non-JSON ({response.status_code})"
                        ) from exc
                    if not isinstance(data, dict):
                        raise KevError("Kev returned a non-object response")
                    return data
                detail = response.text[:300].strip()
                if response.status_code < 500:
                    raise KevError(
                        f"Kev rejected the request with HTTP {response.status_code}: {detail}"
                    )
                last_error = KevError(f"Kev returned HTTP {response.status_code}: {detail}")
            if attempt < self._max_attempts:
                time.sleep(0.4 * attempt)
        raise KevError(
            f"cannot reach Kev at {self.sem.base_url} ({last_error}); "
            "start it with `python -m kev.serve --run <checkpoint> --port 8009`"
        )

    def answer_many(self, state: str, questions: Sequence[Question]) -> JudgeResponse:
        if not questions:
            raise KevError("no semantic questions configured for this endpoint")
        started = time.perf_counter()
        if self.sem.separate:
            answers: dict[str, Answer] = {}
            usage = None
            model = None
            for question in questions:
                data = self._post(
                    {
                        "state": state,
                        "model": self.sem.model,
                        "questions": {question.qid: question.request()},
                    }
                )
                answers.update(self._answers_from(data, [question]))
                model = data.get("model", model)
                usage = data.get("usage", usage)
        else:
            data = self._post(
                {
                    "state": state,
                    "model": self.sem.model,
                    "questions": {question.qid: question.request() for question in questions},
                }
            )
            answers = self._answers_from(data, questions)
            model = data.get("model")
            usage = data.get("usage")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        server_latency = data.get("latency_ms")
        if isinstance(server_latency, (int, float)):
            elapsed_ms = float(server_latency)
        return JudgeResponse(answers=answers, latency_ms=elapsed_ms, model=model, usage=usage)

    @staticmethod
    def _answers_from(data: dict, questions: Sequence[Question]) -> dict[str, Answer]:
        return answers_from_response(data, questions)


def probe(sem: SemConfig, *, transport: httpx.BaseTransport | None = None) -> dict:
    """GET /v1/models: used by `sentinel doctor` to check the server before a run."""
    try:
        with httpx.Client(
            base_url=sem.base_url, timeout=5.0, transport=transport
        ) as client:
            response = client.get("/v1/models")
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise KevError(f"cannot reach Kev at {sem.base_url}: {exc}") from exc
    if not isinstance(data, dict):
        raise KevError("Kev /v1/models returned a non-object response")
    return data