#!/usr/bin/env python3
"""Merge benchmark result files into one model-comparison table.

    uv run python bench/compare_models.py bench/results/kev-0.8b.json bench/results/kev-4b.json

Each input is a JSON file written by `bench/bench.py`. The label comes from
`meta.label` (set with `--label`), falling back to the file name. The rules-only
row is identical across runs and is printed once.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def merge(documents: list[tuple[str, dict]]) -> list[dict]:
    rows: list[dict] = []
    rules_seen = False
    for label, document in documents:
        for result in document.get("results", []):
            if result["backend"] == "rules only":
                if rules_seen:
                    continue
                rules_seen = True
                model = "—"
            else:
                model = label
            rows.append(
                {
                    "model": model,
                    "backend": result["backend"],
                    "detected": result["detected"],
                    "positives": result["positives"],
                    "detection_rate": result["detection_rate"],
                    "false_positives": result["false_positives"],
                    "negatives": result["negatives"],
                    "p50_ms": result.get("p50_ms", 0.0),
                    "mean_ms": result.get("mean_ms", 0.0),
                    "cost_per_1000": result.get("cost_per_1000"),
                    "missed": result.get("missed", []),
                }
            )
    return rows


def markdown(rows: list[dict]) -> str:
    lines = [
        "| Model | Backend | Detection | False positives | p50 per check | Mean | Cost / 1,000 checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        detection = f"{row['detected']}/{row['positives']} ({row['detection_rate']:.0%})"
        fps = f"{row['false_positives']}/{row['negatives']}"
        cost = row["cost_per_1000"]
        cost_cell = (
            f"${cost:.6f}".rstrip("0").rstrip(".") if cost not in (None, 0) else ("$0" if cost == 0 else "—")
        )
        lines.append(
            f"| {row['model']} | {row['backend']} | {detection} | {fps} | "
            f"{row['p50_ms']} ms | {row['mean_ms']} ms | {cost_cell} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", help="bench result JSON files")
    parser.add_argument("--out", help="write the markdown table to a file")
    args = parser.parse_args(argv)

    documents: list[tuple[str, dict]] = []
    for value in args.results:
        path = Path(value)
        document = load(path)
        label = str(document.get("meta", {}).get("label") or path.stem)
        documents.append((label, document))
    table = markdown(merge(documents))
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
