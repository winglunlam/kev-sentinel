"""Data-driven question packs.

Packs live as YAML next to this module, so adding or improving a set of
questions never requires Python changes. `sentinel pack list` and
`sentinel pack show NAME` use these; `sentinel init --pack NAME` seeds a config.

A pack may use the ``{year}`` placeholder in instructions and question ids; it is
replaced with the current year when the pack is loaded.
"""

from __future__ import annotations

import importlib.resources as resources
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml

from ..config import Question, parse_question


@dataclass(frozen=True)
class Pack:
    name: str
    title: str
    description: str
    questions: dict[str, Question]

    def specs(self) -> dict[str, dict[str, Any]]:
        return {qid: question.spec() for qid, question in self.questions.items()}


def _files():
    return sorted(
        (entry for entry in resources.files(__package__).iterdir() if entry.name.endswith(".yaml")),
        key=lambda entry: entry.name,
    )


def _load(entry) -> Pack:
    text = entry.read_text(encoding="utf-8").replace("{year}", str(datetime.now(timezone.utc).year))
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError(f"pack {entry.name} is not a mapping")
    name = str(raw.get("name") or entry.name.removesuffix(".yaml"))
    questions_raw = raw.get("questions")
    if not isinstance(questions_raw, dict) or not questions_raw:
        raise ValueError(f"pack {entry.name} has no questions")
    questions = {
        str(qid): parse_question(str(qid), spec, f"pack {name}.{qid}")
        for qid, spec in questions_raw.items()
    }
    return Pack(
        name=name,
        title=str(raw.get("title") or name),
        description=str(raw.get("description") or ""),
        questions=questions,
    )


def list_packs() -> list[Pack]:
    return [_load(entry) for entry in _files()]


def load_pack(name: str) -> Pack:
    for entry in _files():
        if entry.name.removesuffix(".yaml") == name:
            return _load(entry)
    available = ", ".join(entry.name.removesuffix(".yaml") for entry in _files())
    raise ValueError(f"unknown question pack {name!r}; available: {available}")
