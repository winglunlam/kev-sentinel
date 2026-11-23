from __future__ import annotations

from sentinel.suggest import (
    CURRENCY_CODES,
    error_page_question,
    render_suggestions,
    starter_questions,
    suggest_questions,
)

from .conftest import EXAMPLES, read_json


def _suggest(fixture: str, endpoint: str):
    data = read_json(EXAMPLES / "fixtures" / fixture)
    return {item.qid: item for item in suggest_questions(data, endpoint_name=endpoint)}


def test_jobs_payload_gets_currency_date_and_content_questions():
    suggestions = _suggest("jobs-api.json", "jobs-api")
    assert {"currency", "published_2026", "description_content"} <= set(suggestions)
    assert suggestions["currency"].question.type == "choice"
    assert suggestions["currency"].question.fail_if.raw == "choice != 'usd'"
    assert suggestions["published_2026"].question.fail_if.raw == "p < 0.5"
    assert "role, duties, or requirements" in suggestions["description_content"].question.instructions


def test_pricing_payload_gets_currency_and_updated_date():
    suggestions = _suggest("pricing-api.json", "pricing-api")
    assert set(suggestions) == {"currency", "updated_2026"}
    assert suggestions["currency"].field == "currency"


def test_weather_payload_gets_a_units_question():
    suggestions = _suggest("weather-forecast.json", "weather-forecast")
    assert {"temperatures_plausible", "dates_2026"} <= set(suggestions)
    question = suggestions["temperatures_plausible"].question
    assert "Celsius" in question.instructions
    assert question.fail_if.raw == "p < 0.5"


def test_news_payload_uses_the_news_content_template():
    suggestions = _suggest("news-feed.json", "news-feed")
    assert "published_2026" in suggestions
    assert "summary_content" in suggestions
    assert "news event or report" in suggestions["summary_content"].question.instructions


def test_status_payload_gets_a_status_question():
    suggestions = _suggest("status-page.json", "status-page")
    assert {"overall_status", "dates_2026"} <= set(suggestions)
    question = suggestions["overall_status"].question
    assert question.type == "choice"
    assert question.fail_if.raw == "choice != 'ok'"
    assert "ok" in (question.criteria or {})


def test_error_page_default_is_permissive_enough_for_clean_payloads():
    question = error_page_question()
    assert question.fail_if.raw == "p > 0.25"


def test_starter_questions_always_include_the_guard():
    payload = read_json(EXAMPLES / "fixtures" / "weather-forecast.json")
    questions, suggestions = starter_questions(payload, endpoint_name="weather-forecast")
    assert "error_page" in questions
    assert set(questions) >= {"temperatures_plausible"}
    assert suggestions and suggestions[0].rationale


def test_starter_questions_can_skip_inference():
    payload = read_json(EXAMPLES / "fixtures" / "jobs-api.json")
    questions, suggestions = starter_questions(
        payload, endpoint_name="jobs-api", include_suggestions=False
    )
    assert suggestions == []
    assert set(questions) == {"error_page"}


def test_non_json_payload_gets_no_suggestions():
    assert suggest_questions(None, endpoint_name="x") == []
    assert suggest_questions("plain text", endpoint_name="x") == []


def test_render_suggestions_is_valid_yaml():
    import yaml

    payload = read_json(EXAMPLES / "fixtures" / "jobs-api.json")
    suggestions = suggest_questions(payload, endpoint_name="jobs-api")
    document = yaml.safe_load(render_suggestions(suggestions))
    assert set(document["questions"]) == {item.qid for item in suggestions}


def test_currency_codes_are_uppercase():
    assert all(code == code.upper() for code in CURRENCY_CODES)


def test_suggest_cli_prints_a_yaml_block(tmp_path, monkeypatch, capsys):
    from sentinel.cli import main

    config = tmp_path / "sentinel.yaml"
    fixture = tmp_path / "jobs-api.json"
    fixture.write_text((EXAMPLES / "fixtures" / "jobs-api.json").read_text())
    config.write_text(
        f"""\
version: 1
endpoints:
  - name: jobs-api
    fixture: {fixture.name}
    questions:
      error_page:
        type: noul
        instructions: Is this an error page?
        fail_if: "p > 0.25"
"""
    )
    assert main(["suggest", "jobs-api", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "questions:" in out
    assert "description_content" in out
    assert "sentinel calibrate jobs-api" in out


def test_open_meteo_series_get_unit_range_questions():
    data = read_json(EXAMPLES / "fixtures" / "open-meteo.json")
    suggestions = {
        item.qid: item for item in suggest_questions(data, endpoint_name="weather")
    }
    assert {
        "temperature_2m_plausible",
        "relative_humidity_2m_plausible",
        "wind_speed_10m_plausible",
        "dates_2026",
    } <= set(suggestions)
    assert "degrees Celsius" in suggestions["temperature_2m_plausible"].question.instructions
    assert "percent" in suggestions["relative_humidity_2m_plausible"].question.instructions
    assert "km/h" in suggestions["wind_speed_10m_plausible"].question.instructions
    for item in suggestions.values():
        assert item.question.fail_if.raw.startswith("p <")


def test_scalar_lists_and_counts_do_not_create_bogus_questions():
    from sentinel.core.fingerprint import find_records

    payload = {
        "id": 1,
        "name": "kev",
        "description": "Small decision models you can train and run yourself.",
        "private": False,
        "stargazers_count": 12,
        "forks_count": 3,
        "open_issues_count": 4,
        "updated_at": "2026-11-01T00:00:00Z",
        "license": {"key": "apache-2.0"},
        "topics": ["ml", "decision-models"],
    }
    assert find_records(payload) == (None, None)
    suggestions = {item.qid: item for item in suggest_questions(payload, endpoint_name="github-repo")}
    assert "dates_2026" in suggestions
    assert "Do the dates in this payload fall in 2026?" in suggestions["dates_2026"].question.instructions
    assert not any("temperatures_plausible" == qid for qid in suggestions)
