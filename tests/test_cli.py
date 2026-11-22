from __future__ import annotations

import json

import sentinel.cli as cli
from .conftest import FakeJudge, copy_fixture, noul, read_json, write_json


def _fake_judge_factory(judge: FakeJudge):
    return lambda sem: judge


def test_init_then_check_then_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 0
    assert (tmp_path / "sentinel.yaml").is_file()
    capsys.readouterr()

    assert cli.main(["check", "--no-sem"]) == 0
    assert "1/1 endpoint(s) ok" in capsys.readouterr().out

    data = read_json(fixture)
    for job in data["jobs"]:
        del job["title"]
    write_json(fixture, data)
    assert cli.main(["check", "--no-sem"]) == 1


def test_init_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 0
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 2
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture), "--force"]) == 0


def test_semantic_check_through_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)

    def judge_fn(qid, state):
        if qid == "error_page":
            return noul(qid, 0.02)
        return noul(qid, 0.15 if "luxury watches" in state else 0.9)

    monkeypatch.setattr(cli, "KevClient", _fake_judge_factory(FakeJudge(fn=judge_fn)))
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 0
    assert cli.main(["check"]) == 0

    data = read_json(fixture)
    for job in data["jobs"]:
        job["description"] = "Best deals on luxury watches, diet pills, and cheap holidays."
    write_json(fixture, data)
    assert cli.main(["check"]) == 1


def test_report_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 0
    capsys.readouterr()
    def judge_fn(qid, state):
        if qid == "error_page":
            return noul(qid, 0.02)
        return noul(qid, 0.9)

    monkeypatch.setattr(cli, "KevClient", _fake_judge_factory(FakeJudge(fn=judge_fn)))
    assert cli.main(["check", "--no-alert"]) == 0
    capsys.readouterr()
    assert cli.main(["report", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"][0]["endpoint"] == "jobs-api"
    assert payload["baselines"][0]["set_at"]


def test_chaos_cli_detects_spam(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)

    def judge_fn(qid, state):
        if qid == "error_page":
            return noul(qid, 0.02)
        return noul(qid, 0.1 if "luxury watches" in state else 0.9)

    monkeypatch.setattr(cli, "KevClient", _fake_judge_factory(FakeJudge(fn=judge_fn)))
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 0
    capsys.readouterr()
    assert cli.main(["chaos", "--endpoint", "jobs-api", "--inject", "wrong-entity", "--assert"]) == 0
    assert "1/1 applicable mutations detected" in capsys.readouterr().out


def test_doctor_reports_missing_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    monkeypatch.setattr(cli, "probe", lambda sem: {"data": [{"id": "kev-latest"}]})
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture), "--no-baseline"]) == 0
    assert cli.main(["doctor"]) == 2


def test_unknown_endpoint_is_config_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 0
    assert cli.main(["check", "nope", "--no-sem"]) == 2


def test_baseline_show_and_set(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture), "--no-baseline"]) == 0
    assert cli.main(["baseline", "set", "jobs-api"]) == 0
    capsys.readouterr()
    assert cli.main(["baseline", "show", "jobs-api"]) == 0
    out = capsys.readouterr().out
    assert "jobs-api" in out and "sha256" in out
