"""Configuration loading for ``sentinel.yaml``.

The config is deliberately explicit: every semantic question declares the
condition that should fail, so a threshold is never a hidden default. This is
the file a team reviews in a pull request.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter
from typing import Any, Mapping

import yaml

from .errors import ConfigError
from .sem.types import Answer

CONFIG_NAMES = ("sentinel.yaml", "sentinel.yml")
DATA_DIR_NAME = ".sentinel"

STATE_PLACEHOLDERS = frozenset({"name", "url", "status", "content_type", "payload", "body", "request"})

DEFAULT_STATE_TEMPLATE = """Endpoint: {name}
URL: {url}
HTTP: {status} {content_type}

Payload:
{payload}"""

_TOP_KEYS = {
    "version",
    "sem",
    "endpoints",
    "home",
    "max_state_chars",
    "sample_records",
    "webhook_url",
    "alert_cooldown_s",
    "state",
    "timeout_s",
}
_SEM_KEYS = {"backend", "base_url", "model", "api_key_env", "timeout_s", "separate", "enabled"}
_ENDPOINT_KEYS = {
    "name",
    "watch",
    "url",
    "method",
    "headers",
    "body",
    "fixture",
    "stream_timeout_s",
    "questions",
    "state",
    "notes",
    "allow_extra_fields",
    "expected_status",
    "expect_json",
    "min_items",
    "count_floor",
    "timeout_s",
}
_QUESTION_KEYS = {"type", "instructions", "criteria", "fail_if"}

_METRICS_BY_TYPE = {
    "noul": {"p"},
    "choice": {"choice", "confidence", "p"},
    "score": {"score", "confidence"},
}
_NUMERIC_METRICS = {"p", "score", "confidence"}

_CONDITION_RE = re.compile(
    r"^\s*(p|score|choice|confidence)\s*(==|!=|<=|>=|<|>)\s*(.+?)\s*$", re.IGNORECASE
)


def _check_keys(raw: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _as_str(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _expand(value: str | None) -> str | None:
    if value is None:
        return None
    return os.path.expandvars(value)


@dataclass(frozen=True)
class Condition:
    """A failure condition such as ``p < 0.85`` or ``choice != 'usd'``."""

    metric: str
    op: str
    value: float | str
    raw: str

    def check(self, answer: Answer) -> tuple[bool, str]:
        """Return ``(passed, observed)``; the condition describes when the check *fails*."""
        left = answer.metric(self.metric)
        if isinstance(self.value, str):
            met = left == self.value if self.op == "==" else left != self.value
        else:
            right = float(self.value)
            left = float(left)
            met = {
                "<": left < right,
                "<=": left <= right,
                ">": left > right,
                ">=": left >= right,
                "==": left == right,
                "!=": left != right,
            }[self.op]
        return (not met), answer.describe()

    @classmethod
    def parse(cls, raw: Any, question_type: str, where: str) -> "Condition":
        text = _as_str(raw, f"{where}.fail_if")
        match = _CONDITION_RE.match(text)
        if not match:
            raise ConfigError(
                f"{where}.fail_if: cannot parse {text!r}; expected one of "
                f"`p < 0.85`, `score < 1.5`, `choice != 'usd'`, `confidence < 0.5`"
            )
        metric, op, value_text = match.group(1).lower(), match.group(2), match.group(3).strip()
        allowed = _METRICS_BY_TYPE[question_type]
        if metric not in allowed:
            raise ConfigError(
                f"{where}.fail_if: metric {metric!r} is not available for {question_type} "
                f"questions; use one of {sorted(allowed)}"
            )
        if metric == "choice":
            if op not in {"==", "!="}:
                raise ConfigError(f"{where}.fail_if: choice comparisons support == and !=")
            value: float | str = value_text.strip("'\"")
            if not value:
                raise ConfigError(f"{where}.fail_if: empty choice value")
            return cls(metric, op, value, text)
        value_text = value_text.strip("'\"")
        try:
            number = float(value_text)
        except ValueError as exc:
            raise ConfigError(f"{where}.fail_if: {value_text!r} is not a number") from exc
        if metric in _NUMERIC_METRICS and not 0.0 <= number <= 1.0 and metric != "score":
            raise ConfigError(
                f"{where}.fail_if: {metric} must be within [0, 1], got {number:g}"
            )
        return cls(metric, op, number, text)


@dataclass(frozen=True)
class Question:
    qid: str
    type: str
    instructions: str
    fail_if: Condition
    criteria: Any = None

    def request(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            payload["criteria"] = self.criteria
        return payload

    def spec(self) -> dict[str, Any]:
        """YAML-friendly form, including the failure condition."""
        payload = self.request()
        payload["fail_if"] = self.fail_if.raw
        return payload


@dataclass(frozen=True)
class WatchConfig:
    """Exact-value watch over selected fields of a payload."""

    fields: tuple[str, ...]
    key: str | None = None
    tolerance: float = 0.0
    report_removed: bool = False


@dataclass(frozen=True)
class EndpointConfig:
    name: str
    questions: dict[str, Question]
    url: str | None = None
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None
    fixture: Path | None = None
    stream_timeout_s: float = 12.0
    watch: WatchConfig | None = None
    state: str = DEFAULT_STATE_TEMPLATE
    notes: str = ""
    allow_extra_fields: bool = True
    expected_status: int = 200
    expect_json: bool = True
    min_items: int | None = None
    count_floor: float = 0.5
    timeout_s: float | None = None


@dataclass(frozen=True)
class SemConfig:
    backend: str = "http"
    base_url: str = "http://127.0.0.1:8009"
    model: str = "kev-latest"
    api_key: str | None = None
    timeout_s: float = 60.0
    separate: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    path: Path
    home: Path
    sem: SemConfig
    endpoints: tuple[EndpointConfig, ...]
    max_state_chars: int = 6000
    sample_records: int = 3
    webhook_url: str | None = None
    alert_cooldown_s: int = 3600
    default_timeout_s: float = 15.0
    warnings: tuple[str, ...] = ()

    @property
    def directory(self) -> Path:
        return self.path.parent

    def endpoint(self, name: str) -> EndpointConfig:
        for endpoint in self.endpoints:
            if endpoint.name == name:
                return endpoint
        names = [endpoint.name for endpoint in self.endpoints]
        suggestion = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
        hint = f"; did you mean {suggestion[0]!r}?" if suggestion else ""
        raise ConfigError(
            f"unknown endpoint {name!r}{hint}; configured: {', '.join(names) or '(none)'}"
        )


def find_config(explicit: str | Path | None = None) -> Path:
    """Resolve the config path from an argument, ``$SENTINEL_CONFIG``, or the cwd."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path.resolve()
    env = os.environ.get("SENTINEL_CONFIG")
    if env:
        return find_config(env)
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        for name in CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
    raise ConfigError(
        f"no {CONFIG_NAMES[0]} found in {cwd} or its parents; run `sentinel init` first "
        "or pass --config PATH"
    )


def load_config(path: str | Path | None = None) -> Config:
    config_path = find_config(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{config_path}: expected a mapping at the top level")
    _check_keys(raw, _TOP_KEYS, str(config_path))

    version = raw.get("version", 1)
    if version != 1:
        raise ConfigError(f"{config_path}: unsupported version {version!r} (expected 1)")

    warnings: list[str] = []
    warnings.extend(_validate_template(raw.get("state", DEFAULT_STATE_TEMPLATE), "state"))
    default_state = _as_str(raw.get("state", DEFAULT_STATE_TEMPLATE), "state", allow_empty=True)

    sem = _parse_sem(raw.get("sem") or {}, config_path)

    home_value = raw.get("home")
    home = Path(_expand(_as_str(home_value, "home"))).expanduser() if home_value else config_path.parent / DATA_DIR_NAME
    home = home if home.is_absolute() else (config_path.parent / home)
    home = home.resolve()

    sample_records = raw.get("sample_records", 3)
    if not isinstance(sample_records, int) or sample_records < 1:
        raise ConfigError("sample_records must be a positive integer")
    max_state_chars = raw.get("max_state_chars", 6000)
    if not isinstance(max_state_chars, int) or max_state_chars < 500:
        raise ConfigError("max_state_chars must be an integer >= 500")

    webhook_url = _expand(raw.get("webhook_url"))
    if webhook_url is not None:
        _as_str(webhook_url, "webhook_url")

    cooldown = raw.get("alert_cooldown_s", 3600)
    if not isinstance(cooldown, int) or cooldown < 0:
        raise ConfigError("alert_cooldown_s must be a non-negative integer")

    timeout = raw.get("timeout_s", 15.0)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("timeout_s must be a positive number")

    endpoints_raw = raw.get("endpoints")
    if not isinstance(endpoints_raw, list) or not endpoints_raw:
        raise ConfigError(f"{config_path}: `endpoints` must be a non-empty list")

    endpoints: list[EndpointConfig] = []
    seen: set[str] = set()
    for index, endpoint_raw in enumerate(endpoints_raw):
        endpoint = _parse_endpoint(endpoint_raw, f"endpoints[{index}]", config_path.parent, default_state)
        if endpoint.name in seen:
            raise ConfigError(f"duplicate endpoint name {endpoint.name!r}")
        seen.add(endpoint.name)
        endpoints.append(endpoint)

    return Config(
        path=config_path,
        home=home,
        sem=sem,
        endpoints=tuple(endpoints),
        max_state_chars=max_state_chars,
        sample_records=sample_records,
        webhook_url=webhook_url,
        alert_cooldown_s=cooldown,
        default_timeout_s=float(timeout),
        warnings=tuple(warnings),
    )


def _parse_sem(raw: Mapping[str, Any], config_path: Path) -> SemConfig:
    _check_keys(raw, _SEM_KEYS, f"{config_path}: sem")
    backend = _as_str(raw.get("backend", "http"), "sem.backend")
    if backend != "http":
        raise ConfigError(f"sem.backend: only 'http' is supported, got {backend!r}")
    api_key_env = _as_str(raw.get("api_key_env", "SENTINEL_KEV_API_KEY"), "sem.api_key_env")
    timeout = raw.get("timeout_s", 60.0)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("sem.timeout_s must be a positive number")
    separate = raw.get("separate", False)
    if not isinstance(separate, bool):
        raise ConfigError("sem.separate must be true or false")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("sem.enabled must be true or false")
    base_url = os.environ.get("SENTINEL_KEV_BASE_URL") or raw.get("base_url", "http://127.0.0.1:8009")
    model = os.environ.get("SENTINEL_KEV_MODEL") or raw.get("model", "kev-latest")
    return SemConfig(
        backend=backend,
        base_url=_expand(_as_str(base_url, "sem.base_url")).rstrip("/"),
        model=_as_str(model, "sem.model"),
        api_key=_expand(os.environ.get(api_key_env)),
        timeout_s=float(timeout),
        separate=separate,
        enabled=enabled,
    )


def _validate_template(template: Any, where: str) -> list[str]:
    text = _as_str(template, where, allow_empty=True)
    placeholders = {
        field_name
        for _, field_name, _, _ in Formatter().parse(text)
        if field_name is not None
    }
    unknown = sorted(placeholders - STATE_PLACEHOLDERS)
    if unknown:
        raise ConfigError(
            f"{where}: unknown placeholder(s) {unknown}; available: {sorted(STATE_PLACEHOLDERS)}"
        )
    return []


def _parse_endpoint(
    raw: Any, where: str, config_dir: Path, default_state: str
) -> EndpointConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: expected a mapping")
    _check_keys(raw, _ENDPOINT_KEYS, where)

    name = _as_str(raw.get("name"), f"{where}.name")
    url = raw.get("url")
    if url is not None:
        url = _as_str(url, f"{where}.url")
        if not re.match(r"^https?://", url):
            raise ConfigError(f"{where}.url: must start with http:// or https://")
    fixture_raw = raw.get("fixture")
    fixture: Path | None = None
    if fixture_raw is not None:
        fixture = Path(_as_str(fixture_raw, f"{where}.fixture")).expanduser()
        if not fixture.is_absolute():
            fixture = config_dir / fixture
        fixture = fixture.resolve()
        if not fixture.is_file():
            raise ConfigError(f"{where}.fixture: file not found: {fixture}")
    if url is None and fixture is None:
        raise ConfigError(f"{where}: set `url`, `fixture`, or both")

    method = _as_str(raw.get("method", "GET"), f"{where}.method").upper()
    headers = raw.get("headers") or {}
    if not isinstance(headers, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise ConfigError(f"{where}.headers: expected a mapping of string to string")

    body = raw.get("body")
    if body is not None and not isinstance(body, (str, int, float, bool, dict, list)):
        raise ConfigError(f"{where}.body: expected a string, number, boolean, object, or list")

    state = raw.get("state", default_state)
    _validate_template(state, f"{where}.state")
    state = _as_str(state, f"{where}.state", allow_empty=True)

    expected_status = raw.get("expected_status", 200)
    if not isinstance(expected_status, int) or not 100 <= expected_status <= 599:
        raise ConfigError(f"{where}.expected_status: expected an HTTP status code")

    allow_extra = raw.get("allow_extra_fields", True)
    if not isinstance(allow_extra, bool):
        raise ConfigError(f"{where}.allow_extra_fields must be true or false")
    expect_json = raw.get("expect_json", True)
    if not isinstance(expect_json, bool):
        raise ConfigError(f"{where}.expect_json must be true or false")

    min_items = raw.get("min_items")
    if min_items is not None and (not isinstance(min_items, int) or min_items < 0):
        raise ConfigError(f"{where}.min_items must be a non-negative integer")
    count_floor = raw.get("count_floor", 0.5)
    if not isinstance(count_floor, (int, float)) or not 0 < count_floor <= 1:
        raise ConfigError(f"{where}.count_floor must be in (0, 1]")

    timeout = raw.get("timeout_s")
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ConfigError(f"{where}.timeout_s must be a positive number")
    stream_timeout = raw.get("stream_timeout_s", 12.0)
    if not isinstance(stream_timeout, (int, float)) or stream_timeout <= 0:
        raise ConfigError(f"{where}.stream_timeout_s must be a positive number")

    watch = _parse_watch(raw.get("watch"), f"{where}.watch")

    questions_raw = raw.get("questions")
    if questions_raw is None:
        questions_raw = {}
    if not isinstance(questions_raw, Mapping):
        raise ConfigError(f"{where}.questions: expected a mapping of question id to question")
    if not questions_raw and watch is None:
        raise ConfigError(f"{where}.questions: expected at least one question (or a `watch` block)")
    questions: dict[str, Question] = {}
    for qid, question_raw in questions_raw.items():
        questions[str(qid)] = _parse_question(str(qid), question_raw, f"{where}.questions.{qid}")

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise ConfigError(f"{where}.notes must be a string")

    return EndpointConfig(
        name=name,
        questions=questions,
        url=url,
        method=method,
        headers=dict(headers),
        body=body,
        fixture=fixture,
        state=state,
        notes=notes,
        allow_extra_fields=allow_extra,
        expected_status=expected_status,
        expect_json=expect_json,
        min_items=min_items,
        count_floor=float(count_floor),
        timeout_s=float(timeout) if timeout is not None else None,
        stream_timeout_s=float(stream_timeout),
        watch=watch,
    )


def _parse_watch(raw: Any, where: str) -> WatchConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: expected a mapping")
    _check_keys(raw, {"fields", "key", "tolerance", "report_removed"}, where)
    fields = raw.get("fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(item, str) and item.strip() for item in fields):
        raise ConfigError(f"{where}.fields: expected a non-empty list of dotted field paths")
    key = raw.get("key")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise ConfigError(f"{where}.key: expected a field name (e.g. `id`) or omit it")
    tolerance = raw.get("tolerance", 0.0)
    if not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise ConfigError(f"{where}.tolerance: expected a non-negative number (relative)")
    report_removed = raw.get("report_removed", False)
    if not isinstance(report_removed, bool):
        raise ConfigError(f"{where}.report_removed must be true or false")
    return WatchConfig(
        fields=tuple(item.strip() for item in fields),
        key=key.strip() if isinstance(key, str) else None,
        tolerance=float(tolerance),
        report_removed=report_removed,
    )


def _parse_question(qid: str, raw: Any, where: str) -> Question:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: expected a mapping")
    _check_keys(raw, _QUESTION_KEYS, where)
    qtype = _as_str(raw.get("type"), f"{where}.type").lower()
    if qtype not in _METRICS_BY_TYPE:
        raise ConfigError(f"{where}.type: expected noul, choice, or score, got {qtype!r}")
    instructions = _as_str(raw.get("instructions"), f"{where}.instructions")
    fail_if = Condition.parse(raw.get("fail_if"), qtype, where)
    criteria = raw.get("criteria")
    if qtype == "choice":
        if not isinstance(criteria, Mapping) or not criteria:
            raise ConfigError(f"{where}.criteria: choice questions need a non-empty mapping of option -> description")
        if not all(isinstance(key, str) for key in criteria):
            raise ConfigError(f"{where}.criteria: option names must be strings")
    elif qtype == "score":
        if not isinstance(criteria, list) or not criteria:
            raise ConfigError(f"{where}.criteria: score questions need a non-empty list of levels")
    elif qtype == "noul":
        if criteria is not None and not isinstance(criteria, Mapping):
            raise ConfigError(f"{where}.criteria: noul criteria, when set, must be a mapping")
    return Question(qid=qid, type=qtype, instructions=instructions, fail_if=fail_if, criteria=criteria)


def starter_config(
    name: str,
    *,
    url: str | None = None,
    fixture: Path | None = None,
    fixture_ref: str | None = None,
    questions: Mapping[str, Any] | None = None,
    method: str = "GET",
    body: Any = None,
    headers: Mapping[str, str] | None = None,
    expect_json: bool = True,
    stream_timeout_s: float = 12.0,
) -> str:
    """Config text written by `sentinel init` for one endpoint.

    ``questions`` is an ordered mapping of question id to YAML spec. When omitted,
    the two generic guard questions are used (what `--no-suggest` does).
    """
    if not url and fixture is None:
        raise ConfigError("init needs --url or --fixture")
    endpoint: dict[str, Any] = {"name": name}
    if url:
        endpoint["url"] = url
    if fixture is not None:
        endpoint["fixture"] = fixture_ref or str(fixture)
    if method.upper() != "GET":
        endpoint["method"] = method.upper()
    if headers:
        endpoint["headers"] = dict(headers)
    if body is not None:
        endpoint["body"] = body
    if not expect_json:
        endpoint["expect_json"] = False
    if stream_timeout_s != 12.0:
        endpoint["stream_timeout_s"] = stream_timeout_s
    if questions is not None:
        endpoint["questions"] = dict(questions)
        body = yaml.safe_dump(
            {
                "version": 1,
                "sem": {"base_url": "http://127.0.0.1:8009", "model": "kev-latest"},
                "endpoints": [endpoint],
            },
            sort_keys=False,
            allow_unicode=True,
            width=100,
        )
        header = (
            "# kev-sentinel config. Commit this file; the snapshot database (.sentinel/) is local.\n"
            "#\n"
            "# Questions were inferred from the baseline payload and carry default thresholds.\n"
            "# Run `sentinel calibrate` to replace them with measured ones, then `sentinel chaos`\n"
            "# to prove the layers catch the changes you care about.\n\n"
        )
        return header + body
    endpoint["questions"] = {
        "expected_content": {
            "type": "noul",
            "instructions": (
                f"Does this payload contain the actual content of the {name} endpoint, "
                "rather than an error page, a login or captcha wall, an empty state, or unrelated data?"
            ),
            "fail_if": "p < 0.85",
        },
        "error_page": {
            "type": "noul",
            "instructions": (
                "Does this payload show an error, access-denied, maintenance, or captcha page "
                "instead of the endpoint's normal data?"
            ),
            "fail_if": "p > 0.15",
        },
    }
    body = yaml.safe_dump({"version": 1, "sem": {"base_url": "http://127.0.0.1:8009", "model": "kev-latest"}, "endpoints": [endpoint]}, sort_keys=False, allow_unicode=True)
    header = (
        "# kev-sentinel config. Commit this file; the snapshot database (.sentinel/) is local.\n"
        "#\n"
        "# Every question declares the condition that fails. Thresholds are yours to\n"
        f"# calibrate: run `sentinel chaos --endpoint {name}` to see which injected changes\n"
        "# each layer catches, then tighten `fail_if` until the false-positive rate is\n"
        "# acceptable for your endpoint.\n\n"
    )
    return header + body