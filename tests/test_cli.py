from __future__ import annotations

import json
from pathlib import Path

import sentinel.cli as cli
from .conftest import FakeJudge, choice, copy_fixture, noul, read_json, write_json


def _fake_judge_factory(judge: FakeJudge):
    return lambda sem: judge


def jobs_judge() -> FakeJudge:
    """Answers for the question set `init` infers from the jobs fixture."""

    def fn(qid, state):
        if qid == "currency":
            return choice(qid, "eur" if "EUR" in state else "usd")
        if qid == "description_content":
            return noul(qid, 0.15 if "luxury watches" in state else 0.9)
        if qid == "error_page":
            return noul(qid, 0.02)
        return noul(qid, 0.9)

    return FakeJudge(fn=fn)


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

    monkeypatch.setattr(cli, "KevClient", _fake_judge_factory(jobs_judge()))
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
    monkeypatch.setattr(cli, "KevClient", _fake_judge_factory(jobs_judge()))
    assert cli.main(["check", "--no-alert"]) == 0
    capsys.readouterr()
    assert cli.main(["report", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"][0]["endpoint"] == "jobs-api"
    assert payload["baselines"][0]["set_at"]


def test_chaos_cli_detects_spam(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)

    monkeypatch.setattr(cli, "KevClient", _fake_judge_factory(jobs_judge()))
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
    assert cli.main(["baseline", "show"]) == 0
    out = capsys.readouterr().out
    assert "baselines" in out and "jobs-api" in out


def test_demo_rules_only(tmp_path, monkeypatch, capsys):
    import shutil

    from .conftest import EXAMPLES

    target = tmp_path / "examples"
    shutil.copytree(EXAMPLES, target, ignore=shutil.ignore_patterns(".sentinel"))
    assert cli.main(["demo", "--config", str(target / "sentinel.yaml"), "--no-sem"]) == 0
    assert "rules layer caught it" in capsys.readouterr().out


def test_demo_with_kev(tmp_path, monkeypatch, capsys):
    import shutil

    from .conftest import EXAMPLES

    target = tmp_path / "examples"
    shutil.copytree(EXAMPLES, target, ignore=shutil.ignore_patterns(".sentinel"))

    def fn(qid, state):
        flipped = "outage" in state or "down" in state
        if qid == "salary_currency":
            return choice(qid, "usd")
        if qid == "overall_status":
            return choice(qid, "outage" if flipped else "ok")
        if qid == "all_operational":
            return noul(qid, 0.02 if flipped else 0.98)
        if qid == "reporting_outage":
            return noul(qid, 0.95 if flipped else 0.03)
        return noul(qid, 0.95)

    monkeypatch.setattr(cli, "KevClient", _fake_judge_factory(FakeJudge(fn=fn)))
    assert cli.main(["demo", "--config", str(target / "sentinel.yaml")]) == 0
    assert "Kev caught it" in capsys.readouterr().out


def test_init_can_skip_inference(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture), "--no-suggest"]) == 0
    text = (tmp_path / "sentinel.yaml").read_text()
    assert "expected_content" in text
    assert "description_content" not in text


def test_init_infers_unit_series_questions(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = Path(__file__).resolve().parents[1] / "examples" / "fixtures" / "open-meteo.json"
    fixture = tmp_path / "open-meteo.json"
    fixture.write_text(source.read_text())
    assert cli.main(["init", "weather", "--fixture", str(fixture)]) == 0
    text = (tmp_path / "sentinel.yaml").read_text()
    assert "temperature_2m_plausible" in text
    assert "degrees Celsius" in text
    assert "scale" not in text  # no accidental nonsense


def test_price_watch_runs_without_kev(tmp_path, monkeypatch, capsys):
    import json
    import shutil

    from .conftest import EXAMPLES

    target = tmp_path / "examples"
    shutil.copytree(EXAMPLES, target, ignore=shutil.ignore_patterns(".sentinel"))
    config = target / "llm-prices.yaml"
    fixture = target / "fixtures" / "openrouter-models.json"

    assert cli.main(["baseline", "set", "--config", str(config), "openrouter-models"]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "no semantic questions configured" in out

    data = json.loads(fixture.read_text())
    data["data"][0]["pricing"]["prompt"] = "0.5"
    fixture.write_text(json.dumps(data, indent=2))
    assert cli.main(["check", "--config", str(config)]) == 1
    out = capsys.readouterr().out
    assert "watched value(s) changed" in out
