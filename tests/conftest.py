"""Shared fixtures: no network, no Kev server, no weights."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Sequence

import pytest

from sentinel.config import Question, load_config
from sentinel.sem.client import JudgeResponse
from sentinel.sem.types import Answer

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"


def write_config(tmp_path: Path, body: str, name: str = "sentinel.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def copy_fixture(tmp_path: Path, endpoint: str = "jobs-api") -> Path:
    target = tmp_path / f"{endpoint}.json"
    shutil.copyfile(EXAMPLES / "fixtures" / f"{endpoint}.json", target)
    return target


CONFIG_TEMPLATE = """\
version: 1
sample_records: 2
endpoints:
  - name: jobs-api
    fixture: jobs-api.json
    questions:
      job_duties:
        type: noul
        instructions: Do the job descriptions mention duties or responsibilities?
        fail_if: "p < 0.55"
      salary_currency:
        type: choice
        instructions: Are the salaries in US dollars?
        criteria:
          usd: US dollars
          other: Another currency
        fail_if: "choice != 'usd'"
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> tuple[Path, Path]:
    fixture = copy_fixture(tmp_path)
    config_path = write_config(tmp_path, CONFIG_TEMPLATE)
    return config_path, fixture


@pytest.fixture
def config(config_dir: tuple[Path, Path]):
    config_path, _ = config_dir
    return load_config(config_path)


@pytest.fixture
def examples_config(tmp_path: Path):
    """A private copy of examples/ so tests never touch the repo's .sentinel/."""
    target = tmp_path / "examples"
    shutil.copytree(EXAMPLES, target, ignore=shutil.ignore_patterns(".sentinel"))
    return load_config(target / "sentinel.yaml")


@pytest.fixture
def openmeteo_config(tmp_path: Path):
    target = tmp_path / "examples"
    shutil.copytree(EXAMPLES, target, ignore=shutil.ignore_patterns(".sentinel"))
    return load_config(target / "open-meteo.yaml")


class FakeJudge:
    """Answers from a dict, or from a callable that inspects the state text."""

    def __init__(self, answers: dict[str, Answer] | None = None, fn=None):
        self.answers = answers or {}
        self.fn = fn
        self.calls: list[tuple[str, Sequence[Question]]] = []

    def answer_many(self, state: str, questions: Sequence[Question]) -> JudgeResponse:
        self.calls.append((state, list(questions)))
        out: dict[str, Answer] = {}
        for question in questions:
            if self.fn is not None:
                out[question.qid] = self.fn(question.qid, state)
            else:
                out[question.qid] = self.answers[question.qid]
        return JudgeResponse(answers=out, latency_ms=7.5, model="fake")


def noul(qid: str, p: float) -> Answer:
    return Answer(qid=qid, qtype="noul", value=p)


def choice(qid: str, value: str, confidence: float = 0.8) -> Answer:
    return Answer(qid=qid, qtype="choice", value=value, confidence=confidence, probabilities={value: confidence})


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
