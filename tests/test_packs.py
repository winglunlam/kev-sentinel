"""Question packs: YAML data, not Python heuristics."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import sentinel.cli as cli
from sentinel.config import load_config
from sentinel.packs import list_packs, load_pack

from .conftest import EXAMPLES


def test_pack_list_and_load():
    names = {pack.name for pack in list_packs()}
    assert {"agent", "listings", "news"} <= names

    agent = load_pack("agent")
    assert agent.title == "LLM answer quality"
    assert set(agent.questions) == {"answers_question", "is_refusal", "on_topic"}
    assert agent.questions["is_refusal"].fail_if.raw == "p > 0.5"


def test_year_placeholder_is_substituted():
    news = load_pack("news")
    year = datetime.now(timezone.utc).year
    assert "dates_current" in news.questions
    assert str(year) in news.questions["dates_current"].instructions


def test_unknown_pack_lists_alternatives():
    with pytest.raises(ValueError, match="available:"):
        load_pack("nope")


def test_cli_pack_list_and_show(capsys):
    assert cli.main(["pack", "list"]) == 0
    assert "agent" in capsys.readouterr().out
    assert cli.main(["pack", "show", "agent"]) == 0
    assert "answers_question" in capsys.readouterr().out
    assert cli.main(["pack", "show", "nope"]) == 2


def test_init_with_pack(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = tmp_path / "agent.json"
    fixture.write_text((EXAMPLES / "fixtures" / "agent-reply.json").read_text())
    assert cli.main(["init", "support-agent", "--fixture", str(fixture), "--pack", "agent"]) == 0
    config = load_config(tmp_path / "sentinel.yaml")
    endpoint = config.endpoint("support-agent")
    assert {"answers_question", "is_refusal", "on_topic", "error_page"} <= set(endpoint.questions)
    assert "using question pack" in capsys.readouterr().out
