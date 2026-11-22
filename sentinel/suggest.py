"""Infer a starter question set from a recorded payload.

The first barrier to using sentinel is writing questions. This module looks at a
baseline payload and proposes the questions that catch the usual silent failures:
currency switches, stale dates, status flips, and content replaced by something
else. The heuristics are deliberately conservative — they only suggest a
question when the payload has a field that makes it meaningful.

Suggestions are proposals, not calibrated checks: every one carries a rationale
and a default threshold, and `sentinel calibrate` is how you replace that default
with a measured one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from .config import Condition, Question
from .core.fingerprint import find_records

CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "CNY", "SEK", "NOK", "DKK",
    "INR", "BRL", "MXN", "SGD", "HKD", "NZD", "PLN", "ZAR",
}
CURRENCY_KEYS = ("currency", "curr", "price", "amount", "cost", "salary", "fee", "rate", "total")
STATUS_KEYS = ("status", "state", "health", "mode", "phase", "result", "availability")
PUBLISH_KEYS = ("posted", "published", "created", "updated")
TEXT_KEYS = ("description", "summary", "title", "headline", "content", "body", "abstract", "excerpt", "details")

_DATE_RE = re.compile(r"^(\d{4})-\d{2}-\d{2}([T ].*)?$")

_DOMAIN_TOKENS = {
    "jobs": {
        "job", "role", "hiring", "candidate", "employer", "salary", "engineer",
        "manager", "designer", "developer", "position", "responsibilities",
    },
    "news": {
        "reported", "said", "announced", "according", "government", "percent",
        "official", "statement", "agreement", "company", "week", "election",
    },
}

_NOUNS = {
    "jobs": ("job postings", "job posting"),
    "articles": ("articles", "article"),
    "products": ("products", "product"),
    "services": ("services", "service"),
    "forecast": ("forecast days", "forecast day"),
    "items": ("items", "item"),
    "results": ("results", "result"),
    "data": ("records", "record"),
}


@dataclass(frozen=True)
class Suggestion:
    qid: str
    question: Question
    rationale: str
    field: str
    confidence: str  # "high" | "medium"

    def spec(self) -> dict[str, Any]:
        return {self.qid: self.question.spec()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "type": self.question.type,
            "instructions": self.question.instructions,
            "fail_if": self.question.fail_if.raw,
            "rationale": self.rationale,
            "field": self.field,
            "confidence": self.confidence,
        }


def _question(
    qid: str,
    qtype: str,
    instructions: str,
    fail_if: str,
    *,
    criteria: Any = None,
    rationale: str,
    field: str,
    confidence: str = "high",
) -> Suggestion:
    question = Question(
        qid=qid,
        type=qtype,
        instructions=instructions,
        fail_if=Condition.parse(fail_if, qtype, qid),
        criteria=criteria,
    )
    return Suggestion(
        qid=qid,
        question=question,
        rationale=rationale,
        field=field,
        confidence=confidence,
    )


def _collect_fields(value: Any, records: Iterable[dict[str, Any]]) -> dict[str, list[Any]]:
    fields: dict[str, list[Any]] = {}
    for item in records:
        for key, child in item.items():
            fields.setdefault(str(key), []).append(child)
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(child, (dict, list)):
                fields.setdefault(str(key), []).append(child)
    return fields


def _strings(values: Iterable[Any]) -> list[str]:
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _record_nouns(records_path: str | None, endpoint_name: str) -> tuple[str, str]:
    """Return (plural, singular) nouns for the payload's records."""
    path = (records_path or "").split("[")[0].split(".")[-1]
    if path in _NOUNS:
        return _NOUNS[path]
    name = endpoint_name.lower()
    for token, nouns in (
        ("job", _NOUNS["jobs"]),
        ("news", _NOUNS["articles"]),
        ("article", _NOUNS["articles"]),
        ("price", _NOUNS["products"]),
        ("product", _NOUNS["products"]),
        ("catalog", _NOUNS["products"]),
        ("weather", _NOUNS["forecast"]),
        ("forecast", _NOUNS["forecast"]),
        ("status", _NOUNS["services"]),
        ("health", _NOUNS["services"]),
    ):
        if token in name:
            return nouns
    return "records", "record"


def _currency_suggestion(fields: dict[str, list[Any]]) -> list[Suggestion]:
    for key, values in fields.items():
        lower = key.lower()
        if not any(token in lower for token in ("currency", "curr")):
            continue
        codes = [value.upper() for value in _strings(values) if value.upper() in CURRENCY_CODES]
        if not codes:
            continue
        code = Counter(codes).most_common(1)[0][0]
        return [
            _question(
                "currency",
                "choice",
                "Which currency are the amounts in?",
                f"choice != '{code.lower()}'",
                criteria={
                    code.lower(): f"{code} amounts",
                    "other": "Another currency",
                    "unclear": "Missing, mixed, or unclear",
                },
                rationale=(
                    f"`{key}` is {code} across the baseline. A currency switch keeps every "
                    "field and type intact and silently changes what the numbers mean."
                ),
                field=key,
            )
        ]
    for key, values in fields.items():
        lower = key.lower()
        if not any(token in lower for token in CURRENCY_KEYS):
            continue
        strings = _strings(values)
        if strings and any("$" in value for value in strings):
            return [
                _question(
                    "usd_amounts",
                    "noul",
                    "Are these amounts in US dollars?",
                    "p < 0.7",
                    rationale=(
                        f"`{key}` contains dollar-formatted values. If the feed switches "
                        "currency, the values can keep their shape."
                    ),
                    field=key,
                )
            ]
    return []


def _date_suggestion(
    fields: dict[str, list[Any]], nouns: tuple[str, str], endpoint_name: str
) -> list[Suggestion]:
    candidates: list[tuple[int, str, int, list[str]]] = []
    for key, values in fields.items():
        lower = key.lower()
        if not any(token in lower for token in PUBLISH_KEYS):
            continue
        parsed = []
        for value in _strings(values):
            match = _DATE_RE.match(value)
            if match:
                parsed.append(int(match.group(1)))
        if len(parsed) < max(1, len(values) // 2):
            continue
        year = Counter(parsed).most_common(1)[0][0]
        if not 2000 <= year <= 2100:
            continue
        candidates.append((len(parsed), key, year, parsed))
    if not candidates:
        return []
    _, key, year, _ = max(candidates)
    verb = "updated" if "updated" in key.lower() else "published"
    plural, _singular = nouns
    return [
        _question(
            f"{verb}_{year}",
            "noul",
            f"Were these {plural} {verb} in {year}?",
            "p < 0.5",
            rationale=(
                f"`{key}` is a date field, mostly {year} in the baseline. Stale content keeps "
                "the schema, the keys, and the types; only the dates move."
            ),
            field=key,
        )
    ]


def _freshness_suggestion(value: Any, has_publish_date: bool) -> list[Suggestion]:
    """A payload-wide date question for feeds whose freshness lives in metadata."""
    if has_publish_date:
        return []
    years = []
    for text in json_strings(value):
        match = _DATE_RE.match(text.strip())
        if match:
            years.append(int(match.group(1)))
    if not years:
        return []
    year = Counter(years).most_common(1)[0][0]
    if not 2000 <= year <= 2100:
        return []
    return [
        _question(
            f"dates_{year}",
            "noul",
            f"Do the dates in this payload fall in {year}?",
            "p < 0.5",
            rationale=(
                f"The payload carries dates in {year}. A stale feed or a cached response "
                "keeps every key and type and only moves the dates."
            ),
            field="dates",
        )
    ]


def _status_suggestion(value: Any, fields: dict[str, list[Any]]) -> list[Suggestion]:
    if isinstance(value, dict):
        for key in STATUS_KEYS:
            child = value.get(key)
            if isinstance(child, str) and child.strip():
                status = child.strip()
                return [
                    _question(
                        "overall_status",
                        "choice",
                        "What is the overall status reported by this payload?",
                        f"choice != '{status.lower()}'",
                        criteria={
                            status.lower(): f"'{status}', as in the baseline",
                            "other": "A different status value",
                            "missing": "Missing or unclear",
                        },
                        rationale=(
                            f"The payload's top-level `{key}` is {status!r}. A status flip keeps "
                            "every field and type; only the value changes."
                        ),
                        field=key,
                    )
                ]
    for key, values in fields.items():
        if not any(token in key.lower() for token in STATUS_KEYS):
            continue
        strings = _strings(values)
        if len(strings) < 2:
            continue
        distinct = {value.lower() for value in strings}
        if len(distinct) > 3:
            continue
        status = Counter(strings).most_common(1)[0][0].lower()
        return [
            _question(
                f"{key}_status",
                "choice",
                f"What is the reported `{key}` of these records?",
                f"choice != '{status}'",
                criteria={
                    status: f"'{status}', as in the baseline",
                    "other": "A different status value",
                    "missing": "Missing or unclear",
                },
                rationale=(
                    f"`{key}` is a low-cardinality status field. A change to its dominant "
                    "value is a semantic change the schema cannot see."
                ),
                field=key,
            )
        ]
    return []


def _domain_from_name(endpoint_name: str) -> str | None:
    name = endpoint_name.lower()
    if any(token in name for token in ("job", "career", "hiring", "recruit")):
        return "jobs"
    if any(token in name for token in ("news", "article", "press", "media", "blog")):
        return "news"
    return None


def _detect_domain(value: Any) -> str | None:
    text = json_strings(value)
    lowered = [item.lower() for item in text]
    scores = {}
    for domain, tokens in _DOMAIN_TOKENS.items():
        scores[domain] = sum(1 for token in tokens if any(token in item for item in lowered))
    if not scores:
        return None
    best = max(scores, key=scores.get)
    runner_up = max((score for domain, score in scores.items() if domain != best), default=0)
    if scores[best] >= 3 and scores[best] > runner_up:
        return best
    return None


def json_strings(value: Any) -> list[str]:
    """Every string in a JSON document, depth-first."""
    out: list[str] = []
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _text_suggestion(
    fields: dict[str, list[Any]], nouns: tuple[str, str], domain: str | None
) -> list[Suggestion]:
    best: tuple[float, str, list[str]] | None = None
    for key, values in fields.items():
        lower = key.lower()
        if not any(token in lower for token in TEXT_KEYS):
            continue
        strings = _strings(values)
        if len(strings) < 2:
            continue
        average = sum(len(value) for value in strings) / len(strings)
        if average < 40:
            continue
        if best is None or average > best[0]:
            best = (average, key, strings)
    if best is None:
        return []
    _, key, _ = best
    plural, singular = nouns
    label = key.replace("_", " ")
    if domain == "jobs":
        instructions = (
            f"Do the {label} values describe the role, duties, or requirements of each "
            f"{singular}, rather than advertising or unrelated text?"
        )
        threshold = "p < 0.5"
    elif domain == "news":
        instructions = (
            f"Do the {label} values describe a specific news event or report, rather than "
            "advertising or promotional text?"
        )
        threshold = "p < 0.5"
    else:
        instructions = (
            f"Do the {label} values contain specific, concrete details rather than "
            "advertising or filler?"
        )
        threshold = "p < 0.45"
    return [
        _question(
            f"{key}_content",
            "noul",
            instructions,
            threshold,
            rationale=(
                f"`{key}` is the payload's long-text field. Content replacement (spam, "
                "template text, a different resource) keeps the schema intact; this question "
                "is the only layer that can see it."
            ),
            field=key,
            confidence="medium",
        )
    ]


def _temperature_suggestion(value: Any, fields: dict[str, list[Any]]) -> list[Suggestion]:
    temp_values: list[float] = []
    temp_field = ""
    for key, values in fields.items():
        lower = key.lower()
        if not any(token in lower for token in ("temp", "temperature", "_c", "_f")):
            continue
        numbers = [float(item) for item in values if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if not numbers:
            continue
        temp_field = key
        temp_values.extend(numbers)
    if not temp_values:
        return []
    low = int(min(temp_values) // 5 * 5)
    high = int((max(temp_values) // 5 + 1) * 5)
    units = value.get("units") if isinstance(value, dict) else None
    scale = None
    if isinstance(units, str):
        if units.lower() in {"metric", "celsius", "c"} or "c" in units.lower():
            scale = "Celsius"
        elif units.lower() in {"imperial", "fahrenheit", "f"} or "f" in units.lower():
            scale = "Fahrenheit"
    if scale is None:
        instructions = (
            f"Are the temperatures between {low} and {high}, consistent with the payload's "
            "stated units, rather than values that look converted from another scale?"
        )
    else:
        instructions = (
            f"Are the temperatures between {low} and {high} degrees {scale}, as stated by "
            "the units field?"
        )
    return [
        _question(
            "temperatures_plausible",
            "noul",
            instructions,
            "p < 0.5",
            rationale=(
                f"`{temp_field}` values sit between {min(temp_values):g} and {max(temp_values):g} "
                f"in the baseline. A units regression keeps the label and changes the numbers."
            ),
            field=temp_field,
        )
    ]


def suggest_questions(value: Any, *, endpoint_name: str) -> list[Suggestion]:
    """Propose questions for a JSON payload, most specific first."""
    records_path, records = find_records(value)
    clean_records = [item for item in (records or [])[:50] if isinstance(item, dict)]
    nouns = _record_nouns(records_path, endpoint_name)
    fields = _collect_fields(value, clean_records)

    suggestions: list[Suggestion] = []
    suggestions.extend(_currency_suggestion(fields))
    date_suggestions = _date_suggestion(fields, nouns, endpoint_name)
    suggestions.extend(date_suggestions)
    suggestions.extend(_status_suggestion(value, fields))
    suggestions.extend(_temperature_suggestion(value, fields))
    suggestions.extend(_freshness_suggestion(value, has_publish_date=bool(date_suggestions)))
    domain = _detect_domain(value) or _domain_from_name(endpoint_name)
    suggestions.extend(_text_suggestion(fields, nouns, domain))

    seen: set[str] = set()
    unique: list[Suggestion] = []
    for suggestion in suggestions:
        if suggestion.qid in seen:
            continue
        seen.add(suggestion.qid)
        unique.append(suggestion)
    return unique


def error_page_question() -> Question:
    """The one question every starter config gets, regardless of payload shape."""
    return Question(
        qid="error_page",
        type="noul",
        instructions=(
            "Does this payload show an error, access-denied, maintenance, or captcha page "
            "instead of the endpoint's normal data?"
        ),
        fail_if=Condition.parse("p > 0.25", "noul", "error_page"),
    )


def starter_questions(
    value: Any | None, *, endpoint_name: str, include_suggestions: bool = True
) -> tuple[dict[str, dict[str, Any]], list[Suggestion]]:
    """Questions for a new config: inferred suggestions plus the error-page guard."""
    suggestions = (
        suggest_questions(value, endpoint_name=endpoint_name)
        if include_suggestions and value is not None
        else []
    )
    questions: dict[str, dict[str, Any]] = {item.qid: item.question.spec() for item in suggestions}
    questions["error_page"] = error_page_question().spec()
    return questions, suggestions


def render_suggestions(suggestions: Iterable[Suggestion]) -> str:
    """A copy-pasteable YAML block for `sentinel suggest`."""
    import yaml

    spec = {item.qid: item.question.spec() for item in suggestions}
    body = yaml.safe_dump({"questions": spec}, sort_keys=False, allow_unicode=True, width=100)
    notes = "\n".join(f"# {item.qid}: {item.rationale}" for item in suggestions)
    return (notes + "\n" if notes else "") + body