from __future__ import annotations

from sentinel.core.fingerprint import compare_fingerprints, fingerprint, find_records


def sample():
    return {
        "meta": {"page": 1, "total": 10},
        "jobs": [
            {"id": "a", "title": "Engineer", "salary": 100, "remote": True},
            {"id": "b", "title": "Designer", "salary": 90, "remote": False},
        ],
    }


def test_paths_types_and_records():
    fp = fingerprint(sample())
    assert fp["records_path"] == "jobs"
    assert fp["item_count"] == 2
    assert fp["paths"]["jobs[].title"] == ["string"]
    assert fp["paths"]["jobs[].remote"] == ["boolean"]
    assert fp["paths"]["meta.page"] == ["number"]


def test_root_array_records_path():
    fp = fingerprint([{"a": 1}, {"a": 2}])
    assert fp["records_path"] == ""
    assert fp["item_count"] == 2
    assert fp["paths"]["[].a"] == ["number"]


def test_extra_fields_are_ignored_when_allowed():
    base = fingerprint(sample())
    changed = sample()
    changed["jobs"][0]["team"] = "platform"
    diff = compare_fingerprints(base, fingerprint(changed))
    assert not diff.breaking
    assert diff.extra == ("jobs[].team",)


def test_extra_fields_are_reported():
    base = fingerprint(sample())
    changed = sample()
    changed["jobs"][0]["team"] = "platform"
    diff = compare_fingerprints(base, fingerprint(changed))
    assert "jobs[].team" in diff.extra
    assert not diff.breaking


def test_removed_field_is_breaking():
    base = fingerprint(sample())
    changed = sample()
    for item in changed["jobs"]:
        del item["title"]
    diff = compare_fingerprints(base, fingerprint(changed))
    assert diff.breaking
    assert "jobs[].title" in diff.missing


def test_type_change_is_breaking():
    base = fingerprint(sample())
    changed = sample()
    changed["jobs"][0]["salary"] = "100"
    changed["jobs"][1]["salary"] = "90"
    diff = compare_fingerprints(base, fingerprint(changed))
    assert diff.retyped and diff.retyped[0][0] == "jobs[].salary"


def test_null_flood_is_breaking():
    base = fingerprint(sample())
    changed = sample()
    for item in changed["jobs"]:
        item["title"] = None
    diff = compare_fingerprints(base, fingerprint(changed))
    assert "jobs[].title" in diff.nulled


def test_records_path_change():
    base = fingerprint(sample())
    changed = {"meta": {"page": 1}, "results": [{"id": "a"}]}
    diff = compare_fingerprints(base, fingerprint(changed))
    assert diff.records_path_changed == ("jobs", "results")


def test_find_records_prefers_first_object_list():
    path, records = find_records({"tags": ["a", "b"], "items": [{"x": 1}]})
    assert path == "items" and records == [{"x": 1}]
