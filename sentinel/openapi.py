"""Import OpenAPI 3 operations as sentinel endpoints.

This is the structured alternative to guessing questions from a payload: an
OpenAPI document already declares the fields, formats, enums, bounds and often an
example response. The importer turns that into endpoints, watches and questions,
then you run `sentinel calibrate`/`preflight` as usual.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import ConfigError
from .core.fingerprint import find_records
from .suggest import error_page_question, suggest_questions

_DATE_FORMATS = {"date", "date-time"}
_METHODS = ("get", "post", "put", "patch")


@dataclass
class ImportedEndpoint:
    name: str
    url: str
    method: str
    questions: dict[str, dict[str, Any]]
    body: Any = None
    watch: dict[str, Any] | None = None
    notes: str = ""

    def to_config(self) -> dict[str, Any]:
        endpoint: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
            "method": self.method,
        }
        if self.body is not None:
            endpoint["body"] = self.body
        if self.watch is not None:
            endpoint["watch"] = self.watch
        endpoint["questions"] = self.questions
        if self.notes:
            endpoint["notes"] = self.notes
        return endpoint


def load_spec(source: str | Path, *, headers: dict[str, str] | None = None, timeout: float = 20.0) -> dict:
    text = str(source)
    if re.match(r"^https?://", text):
        try:
            response = httpx.get(text, headers=headers, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConfigError(f"cannot fetch OpenAPI document {text}: {exc}") from exc
        body = response.text
    else:
        path = Path(text).expanduser()
        if not path.is_file():
            raise ConfigError(f"OpenAPI file not found: {path}")
        body = path.read_text(encoding="utf-8")
    try:
        document = json.loads(body)
    except ValueError:
        try:
            document = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            raise ConfigError(f"OpenAPI document is not JSON or YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError("OpenAPI document must be an object")
    if not isinstance(document.get("paths"), dict):
        raise ConfigError("OpenAPI document has no `paths` object")
    return document


def _resolve(spec: dict, value: Any, depth: int = 0) -> Any:
    if depth > 20 or not isinstance(value, dict) or "$ref" not in value:
        return value
    ref = str(value["$ref"])
    if not ref.startswith("#/"):
        raise ConfigError(f"external $ref is not supported: {ref}")
    node: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"unresolvable $ref: {ref}")
        node = node[part]
    return _resolve(spec, node, depth + 1)


def _first_json_media(content: dict) -> dict | None:
    for media_type, media in (content or {}).items():
        if "json" in str(media_type).lower() or str(media_type) in {"*/*", ""}:
            return media if isinstance(media, dict) else None
    return None


def _response_schema(spec: dict, operation: dict) -> tuple[dict | None, Any]:
    responses = operation.get("responses") or {}
    codes = sorted(
        (code for code in responses if str(code).startswith("2")),
        key=lambda code: (str(code) != "200", str(code)),
    )
    for code in codes:
        response = _resolve(spec, responses[code])
        if not isinstance(response, dict):
            continue
        media = _first_json_media(response.get("content") or {})
        if media is None:
            continue
        schema = _resolve(spec, media.get("schema")) if media.get("schema") else None
        return (schema if isinstance(schema, dict) else None), _media_example(spec, media)
    return None, None


def _media_example(spec: dict, media: dict) -> Any:
    if "example" in media:
        return media["example"]
    examples = media.get("examples")
    if isinstance(examples, dict) and examples:
        first = _resolve(spec, next(iter(examples.values())))
        if isinstance(first, dict) and "value" in first:
            return first["value"]
    schema = _resolve(spec, media.get("schema")) if media.get("schema") else None
    if isinstance(schema, dict):
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
    return None


def _path_parameters(spec: dict, path_item: dict, operation: dict) -> dict[str, Any] | None:
    parameters = []
    for source in (path_item.get("parameters") or [], operation.get("parameters") or []):
        parameters.extend(_resolve(spec, item) for item in source)
    values: dict[str, Any] = {}
    for parameter in parameters:
        if not isinstance(parameter, dict) or parameter.get("in") != "path":
            continue
        schema = _resolve(spec, parameter.get("schema")) if isinstance(parameter.get("schema"), dict) else {}
        value = (
            parameter.get("example")
            if "example" in parameter
            else schema.get("example")
            if isinstance(schema, dict) and "example" in schema
            else schema.get("default")
            if isinstance(schema, dict)
            else None
        )
        if value is not None:
            values[str(parameter.get("name"))] = value
    return values


def _request_body(spec: dict, operation: dict) -> Any:
    request = operation.get("requestBody")
    if not isinstance(request, dict):
        return None
    request = _resolve(spec, request)
    media = _first_json_media(request.get("content") or {})
    if media is None:
        return None
    if "example" in media:
        return media["example"]
    schema = _resolve(spec, media.get("schema")) if media.get("schema") else None
    if isinstance(schema, dict):
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
    examples = media.get("examples")
    if isinstance(examples, dict) and examples:
        first = _resolve(spec, next(iter(examples.values())))
        if isinstance(first, dict) and "value" in first:
            return first["value"]
    return None


def _schema_fields(spec: dict, schema: Any, prefix: str = "") -> list[tuple[str, dict]]:
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict):
        return []
    fields: list[tuple[str, dict]] = []
    for name, prop in (schema.get("properties") or {}).items():
        prop = _resolve(spec, prop)
        if not isinstance(prop, dict):
            continue
        label = f"{prefix}{name}"
        fields.append((label, prop))
        if prop.get("type") == "object":
            fields.extend(_schema_fields(spec, prop, f"{label}."))
        items = _resolve(spec, prop.get("items")) if isinstance(prop.get("items"), dict) else None
        if prop.get("type") == "array" and isinstance(items, dict) and items.get("type") == "object":
            fields.extend(_schema_fields(spec, items, f"{label}[]."))
    return fields


def _schema_questions(spec: dict, schema: dict | None, year: int) -> dict[str, dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {}
    if not isinstance(schema, dict):
        return questions
    fields = _schema_fields(spec, schema)
    has_dates = any(
        prop.get("format") in _DATE_FORMATS or str(prop.get("format", "")).startswith("date")
        for _label, prop in fields
    )
    if has_dates:
        questions[f"dates_{year}"] = {
            "type": "noul",
            "instructions": f"Do the dates in this payload fall in {year}?",
            "fail_if": "p < 0.5",
        }
    for label, prop in fields:
        name = label.lower()
        enum = [str(item) for item in prop.get("enum") or []]
        if "currency" in name and any(item.upper() == "USD" for item in enum):
            questions["currency"] = {
                "type": "noul",
                "instructions": "Are the amounts in US dollars?",
                "fail_if": "p < 0.7",
            }
        if prop.get("type") in {"number", "integer"} and "minimum" in prop and "maximum" in prop:
            low, high = prop["minimum"], prop["maximum"]
            qid = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") + "_range"
            questions[qid] = {
                "type": "noul",
                "instructions": (
                    f"Are the {label.replace('.', ' ')} values between {low} and {high}?"
                ),
                "fail_if": "p < 0.5",
            }
    return questions


def _watch_from_example(example: Any) -> dict[str, Any] | None:
    _path, records = find_records(example)
    items = [item for item in (records or []) if isinstance(item, dict)]
    if not items:
        return None
    key = next((name for name in ("id", "sku", "code", "name") if name in items[0]), None)
    if key is None:
        return None
    fields: set[str] = set()
    for item in items[:20]:
        for name, value in item.items():
            lower = str(name).lower()
            if lower in {"price", "cost"} or "price" in lower or "cost" in lower:
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    fields.add(str(name))
            if isinstance(value, dict):
                for child in value:
                    child_lower = str(child).lower()
                    if child_lower in {"prompt", "completion", "price", "cost"}:
                        fields.add(f"{name}.{child}")
    if not fields:
        return None
    return {"key": key, "fields": sorted(fields), "tolerance": 0.0}


def _endpoint_name(operation: dict, method: str, path: str) -> str:
    raw = str(operation.get("operationId") or f"{method}-{path.strip('/')}")
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", raw)
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower() or f"{method}-endpoint"


def endpoints_from_spec(
    spec: dict,
    *,
    base_url: str | None = None,
    methods: tuple[str, ...] = _METHODS,
    limit: int | None = None,
) -> tuple[list[ImportedEndpoint], list[str]]:
    servers = spec.get("servers") or []
    base = base_url or (str(servers[0].get("url")) if servers and isinstance(servers[0], dict) else "")
    if not base:
        raise ConfigError("OpenAPI document has no servers[].url; pass --base-url")
    if not base.startswith(("http://", "https://")):
        raise ConfigError(f"server URL must be absolute, got {base!r}")
    year = datetime.now(timezone.utc).year
    endpoints: list[ImportedEndpoint] = []
    skipped: list[str] = []
    seen: set[str] = set()

    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in methods:
            operation = path_item.get(method)
            if not isinstance(operation, dict) or operation.get("deprecated"):
                continue
            parameters = _path_parameters(spec, path_item, operation)
            if parameters is None:
                continue
            missing = re.findall(r"\{([^}]+)\}", path)
            if any(name not in parameters for name in missing):
                skipped.append(f"{method.upper()} {path}: no example for path parameter(s) {missing}")
                continue
            url = base.rstrip("/") + path
            for name, value in parameters.items():
                url = url.replace("{" + name + "}", str(value))
            body = _request_body(spec, operation)
            if method in {"post", "put", "patch"} and body is None:
                skipped.append(f"{method.upper()} {path}: no JSON request body example")
                continue
            schema, example = _response_schema(spec, operation)
            questions: dict[str, dict[str, Any]] = {}
            note = ""
            if example is not None:
                suggestions = suggest_questions(example, endpoint_name=_endpoint_name(operation, method, path))
                questions = {item.qid: item.question.spec() for item in suggestions}
                note = "questions inferred from the example response in the OpenAPI document"
            if not questions:
                questions = _schema_questions(spec, schema, year)
                note = "questions derived from the response schema; run `sentinel suggest` after a baseline for more"
            questions.setdefault("error_page", error_page_question().spec())
            name = _endpoint_name(operation, method, path)
            counter = 2
            while name in seen:
                name = f"{_endpoint_name(operation, method, path)}-{counter}"
                counter += 1
            seen.add(name)
            endpoints.append(
                ImportedEndpoint(
                    name=name,
                    url=url,
                    method=method.upper(),
                    questions=questions,
                    body=body,
                    watch=_watch_from_example(example) if example is not None else None,
                    notes=note,
                )
            )
            if limit is not None and len(endpoints) >= limit:
                return endpoints, skipped
    return endpoints, skipped


def config_yaml(endpoints: list[ImportedEndpoint]) -> str:
    document = {
        "version": 1,
        "sem": {"base_url": "http://127.0.0.1:8009", "model": "kev-latest"},
        "endpoints": [endpoint.to_config() for endpoint in endpoints],
    }
    header = (
        "# kev-sentinel config generated from an OpenAPI document.\n"
        "# Run `sentinel baseline set`, `sentinel calibrate`, `sentinel chaos` and\n"
        "# `sentinel preflight` before scheduling it.\n\n"
    )
    return header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
