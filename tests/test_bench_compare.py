"""The model-comparison report that merges bench result files."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compare = _load("bench_compare", REPO / "bench" / "compare_models.py")


def _document(label: str, detected: int, p50: float) -> dict:
    return {
        "meta": {"label": label},
        "results": [
            {
                "backend": "rules only",
                "detected": 68,
                "positives": 96,
                "detection_rate": 68 / 96,
                "false_positives": 0,
                "negatives": 10,
                "p50_ms": 0.0,
                "mean_ms": 0.1,
                "cost_per_1000": 0,
                "missed": [],
            },
            {
                "backend": "kev + rules",
                "detected": detected,
                "positives": 96,
                "detection_rate": detected / 96,
                "false_positives": 0,
                "negatives": 10,
                "p50_ms": p50,
                "mean_ms": p50 * 3,
                "cost_per_1000": 0.002,
                "missed": ["wrong-entity"],
            },
        ],
    }


def test_merge_dedupes_rules_row_and_labels_models(tmp_path):
    small = tmp_path / "kev-0.8b.json"
    large = tmp_path / "kev-4b.json"
    small.write_text(json.dumps(_document("kev-0.8b", 90, 135.0)))
    large.write_text(json.dumps(_document("other-checkpoint", 93, 420.0)))

    documents = [("kev-0.8b", compare.load(small)), ("other-checkpoint", compare.load(large))]
    rows = compare.merge(documents)
    assert [row["model"] for row in rows] == ["—", "kev-0.8b", "other-checkpoint"]
    assert rows[1]["detected"] == 90 and rows[2]["detected"] == 93

    table = compare.markdown(rows)
    assert "kev-0.8b" in table and "other-checkpoint" in table
    assert "90/96 (94%)" in table
    assert table.count("| — | rules only |") == 1


def test_main_writes_the_table(tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_document("kev-0.8b", 90, 135.0)))
    out = tmp_path / "table.md"
    assert compare.main([str(path), "--out", str(out)]) == 0
    assert "kev-0.8b" in capsys.readouterr().out
    assert "Detection" in out.read_text()
