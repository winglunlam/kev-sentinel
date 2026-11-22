"""Answer types returned by Kev and the metric lookup used by failure conditions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Answer:
    """One Kev answer.

    ``value`` is the probability of "true" for a noul question, the chosen option
    name for a choice question, and the expected level for a score question.
    """

    qid: str
    qtype: str
    value: float | str
    confidence: float | None = None
    probabilities: dict[str, float] | None = None

    def metric(self, name: str) -> float | str:
        """Resolve a condition metric (``p``, ``score``, ``choice``, ``confidence``)."""
        if name == "p":
            if self.qtype == "noul":
                return float(self.value)
            if self.qtype == "choice":
                probs = self.probabilities or {}
                return float(probs.get(str(self.value), 0.0))
        elif name == "score" and self.qtype == "score":
            return float(self.value)
        elif name == "choice" and self.qtype == "choice":
            return str(self.value)
        elif name == "confidence":
            if self.confidence is None:
                raise ValueError(f"question {self.qid!r} has no confidence")
            return float(self.confidence)
        raise ValueError(f"metric {name!r} is not defined for {self.qtype} answers")

    def describe(self) -> str:
        if self.qtype == "noul":
            return f"p(true)={float(self.value):.2f}"
        if self.qtype == "choice":
            suffix = f" ({self.confidence:.2f})" if self.confidence is not None else ""
            return f"choice={self.value}{suffix}"
        if self.qtype == "score":
            suffix = f" ({self.confidence:.2f})" if self.confidence is not None else ""
            return f"score={float(self.value):.2f}{suffix}"
        return str(self.value)