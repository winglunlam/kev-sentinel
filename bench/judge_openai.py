#!/usr/bin/env python3
"""External judge adapter for `bench/bench.py --judge-cmd`.

Reads one System One-shaped request on stdin, asks an OpenAI-compatible chat
completions endpoint to answer every question, and prints System One-shaped
answers on stdout. This is how you put a cloud LLM in the benchmark table
without changing the harness.

Environment:

    OPENAI_API_KEY    required
    OPENAI_BASE_URL   default https://api.openai.com/v1
    OPENAI_MODEL      default gpt-4o-mini
    OPENAI_TIMEOUT    seconds, default 60

The prompt asks for one JSON object with a probability or label per question.
It is a reasonable baseline, not a tuned one: the point is to compare cost and
detection on the same labelled set, not to win a prompt contest.

Usage:

    uv run python bench/bench.py --config examples/sentinel.yaml \
        --judge-cmd "python bench/judge_openai.py"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

SYSTEM_PROMPT = """\
You are answering diagnostic questions about an API payload.
Return one JSON object and nothing else.

Schema:
{"answers": {"<question id>": <answer>, ...}}

Answer shapes by question type:
- noul:   {"type": "noul", "noul": <probability between 0 and 1>}
- choice: {"type": "choice", "choice": "<one of the criteria keys>",
           "confidence": <0 to 1>, "probabilities": {"<key>": <0 to 1>, ...}}
- score:  {"type": "score", "score": <expected level, 0-based>,
           "confidence": <0 to 1>}

Answer every question independently. Probabilities must sum to 1.
"""


def build_messages(payload: dict) -> list[dict]:
    lines = ["STATE:", str(payload.get("state", "")), "", "QUESTIONS:"]
    for qid, question in payload["questions"].items():
        lines.append(f"- id: {qid}")
        lines.append(f"  type: {question.get('type')}")
        instructions = question.get("instructions")
        if instructions:
            lines.append(f"  instructions: {instructions}")
        criteria = question.get("criteria")
        if criteria:
            lines.append(f"  criteria: {json.dumps(criteria, ensure_ascii=False)}")
    lines.append("")
    lines.append("Answer every question above. Return the JSON object only.")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_answers(raw: str, questions: dict) -> dict:
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"model did not return JSON: {exc}") from exc
    answers = document.get("answers") if isinstance(document, dict) else None
    if not isinstance(answers, dict):
        raise ValueError("model response has no answers object")

    cleaned: dict = {}
    for qid, question in questions.items():
        answer = answers.get(qid)
        if not isinstance(answer, dict):
            raise ValueError(f"model did not answer {qid!r}")
        qtype = question.get("type")
        if qtype == "noul":
            value = answer.get("noul")
            if not isinstance(value, (int, float)):
                raise ValueError(f"{qid}: noul answer is not a number")
            value = min(1.0, max(0.0, float(value)))
            cleaned[qid] = {"type": "noul", "noul": round(value, 4)}
        elif qtype == "choice":
            criteria = question.get("criteria") or {}
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in criteria:
                raise ValueError(f"{qid}: choice {choice!r} is not one of the criteria keys")
            probabilities = answer.get("probabilities")
            probabilities = (
                {key: float(value) for key, value in probabilities.items() if isinstance(value, (int, float))}
                if isinstance(probabilities, dict)
                else {choice: 1.0}
            )
            confidence = answer.get("confidence")
            cleaned[qid] = {
                "type": "choice",
                "choice": choice,
                "confidence": float(confidence) if isinstance(confidence, (int, float)) else 0.0,
                "probabilities": {key: round(value, 4) for key, value in probabilities.items()},
            }
        elif qtype == "score":
            value = answer.get("score")
            if not isinstance(value, (int, float)):
                raise ValueError(f"{qid}: score answer is not a number")
            cleaned[qid] = {
                "type": "score",
                "score": round(float(value), 4),
                "confidence": float(answer.get("confidence", 0.0))
                if isinstance(answer.get("confidence", 0.0), (int, float))
                else 0.0,
            }
        else:
            raise ValueError(f"{qid}: unknown question type {qtype!r}")
    return cleaned


def call_model(messages: list[dict], *, base_url: str, api_key: str, model: str, timeout: float) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
    ).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"provider returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach provider: {exc.reason}") from exc
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise RuntimeError("provider response has no choices")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str):
        raise RuntimeError("provider response has no text content")
    usage = payload.get("usage")
    if isinstance(usage, dict):
        print(
            f"usage: prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')}",
            file=sys.stderr,
        )
    return content


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    timeout = float(os.environ.get("OPENAI_TIMEOUT", "60"))
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        print(f"stdin is not JSON: {exc}", file=sys.stderr)
        return 2
    try:
        messages = build_messages(payload)
        content = call_model(
            messages, base_url=base_url, api_key=api_key, model=model, timeout=timeout
        )
        answers = parse_answers(content, payload["questions"])
    except (ValueError, RuntimeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    json.dump({"answers": answers}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())