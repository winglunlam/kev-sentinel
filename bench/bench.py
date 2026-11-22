"""Measure detection, latency, and cost per 1,000 checks.

Every number in the README's benchmark table comes from running this script.
It builds a labelled set from the example fixtures: each clean payload is a
negative, and each chaos mutation is a positive. Both layers run over the same
set, so the table compares like with like.

Usage:
    uv run python bench/bench.py --config examples/sentinel.yaml
    uv run python bench/bench.py --config examples/sentinel.yaml --judge-cmd ./bench/my-cloud-judge.sh
    uv run python bench/bench.py --json bench/results/kev-0.8b.json --markdown bench/results/kev-0.8b.md

The optional --judge-cmd protocol: one JSON object on stdin

    {"state": "...", "questions": {"qid": {"type": "noul", "instructions": "..."}}}

and one JSON object on stdout

    {"answers": {"qid": {"type": "noul", "noul": 0.12}}}

That is the System One shape, so a cloud baseline needs a small adapter, not a
fork of this file.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from sentinel.chaos import MUTATORS  # noqa: E402
from sentinel.config import Config, EndpointConfig, load_config  # noqa: E402
from sentinel.core.fetch import FetchResult, fetch_endpoint, synthetic_result  # noqa: E402
from sentinel.core.fingerprint import fingerprint_result  # noqa: E402
from sentinel.core.state import build_state  # noqa: E402
from sentinel.core.store import Snapshot  # noqa: E402
from sentinel.errors import KevError  # noqa: E402
from sentinel.rules.deterministic import blocking_findings, run_rules  # noqa: E402
from sentinel.sem.client import KevClient, answers_from_response  # noqa: E402
from sentinel.sem.evaluate import evaluate_answers  # noqa: E402


@dataclass
class Case:
    endpoint: EndpointConfig
    baseline: Snapshot
    result: FetchResult
    label: int  # 0 clean, 1 drifted
    mutation: str


@dataclass
class Outcome:
    detected: bool
    rules_ms: float
    judge_ms: float


class SubprocessJudge:
    """Route semantic checks to an external command (a cloud baseline, usually)."""

    def __init__(self, command: str):
        self.command = command

    def answer_many(self, state, questions):
        payload = {
            "state": state,
            "questions": {question.qid: question.request() for question in questions},
        }
        started = time.perf_counter()
        process = subprocess.run(
            self.command,
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        if process.returncode != 0:
            raise KevError(f"judge command failed: {process.stderr[:300]}")
        try:
            data = json.loads(process.stdout)
        except ValueError as exc:
            raise KevError(f"judge command did not print JSON: {exc}") from exc
        from sentinel.sem.client import JudgeResponse

        return JudgeResponse(
            answers=answers_from_response(data, questions), latency_ms=elapsed
        )


def snapshot_of(endpoint: EndpointConfig, result: FetchResult) -> Snapshot:
    return Snapshot(
        id=0,
        endpoint=endpoint.name,
        ts="",
        status=result.status,
        content_type=result.content_type,
        body=result.text,
        body_truncated=False,
        sha256="",
        fingerprint=fingerprint_result(result),
        source=result.source,
    )


def case_set(config: Config, endpoints: Sequence[EndpointConfig]) -> list[Case]:
    cases: list[Case] = []
    for endpoint in endpoints:
        clean = fetch_endpoint(endpoint, timeout_s=config.default_timeout_s)
        if not clean.ok or clean.json is None:
            print(f"skip {endpoint.name}: {clean.error or clean.json_error}", file=sys.stderr)
            continue
        baseline = snapshot_of(endpoint, clean)
        cases.append(Case(endpoint, baseline, clean, 0, "clean"))
        for name, mutator in MUTATORS.items():
            mutation = mutator.apply(clean, {}, name)
            mutated = synthetic_result(
                endpoint.name,
                body=mutation.body,
                status=mutation.status,
                content_type=mutation.content_type,
            )
            changed = (
                mutation.status != clean.status
                or mutation.content_type != clean.content_type
                or mutated.json != clean.json
            )
            if not changed:
                continue
            cases.append(Case(endpoint, baseline, mutated, 1, name))
    return cases


def run_case(config: Config, case: Case, *, judge=None, use_kev: bool) -> Outcome:
    started = time.perf_counter()
    findings = run_rules(case.endpoint, case.result, case.baseline)
    rules_ms = (time.perf_counter() - started) * 1000.0
    judge_ms = 0.0
    sem_failed = False
    if use_kev and judge is not None and case.result.json is not None and not _is_html(case.result.text):
        state = build_state(
            case.endpoint,
            case.result,
            sample_records=config.sample_records,
            max_chars=config.max_state_chars,
        )
        began = time.perf_counter()
        response = judge.answer_many(state, list(case.endpoint.questions.values()))
        judge_ms = (time.perf_counter() - began) * 1000.0
        results = evaluate_answers(case.endpoint, response)
        sem_failed = any(not item.passed for item in results)
    return Outcome(
        detected=bool(blocking_findings(findings)) or sem_failed,
        rules_ms=rules_ms,
        judge_ms=judge_ms,
    )


def _is_html(text: str) -> bool:
    from sentinel.rules.deterministic import looks_like_html

    return looks_like_html(text)


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def summarize(outcomes: Sequence[tuple[Case, Outcome]], label: str) -> dict:
    latencies = [outcome.rules_ms + outcome.judge_ms for _, outcome in outcomes]
    positives = [(case, outcome) for case, outcome in outcomes if case.label == 1]
    negatives = [(case, outcome) for case, outcome in outcomes if case.label == 0]
    detected = [case for case, outcome in positives if outcome.detected]
    false_positives = [case for case, outcome in negatives if outcome.detected]
    return {
        "backend": label,
        "positives": len(positives),
        "detected": len(detected),
        "detection_rate": len(detected) / len(positives) if positives else 0.0,
        "negatives": len(negatives),
        "false_positives": len(false_positives),
        "p50_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "p95_ms": round(percentile(latencies, 95), 1),
        "mean_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
        "missed": sorted({case.mutation for case, outcome in positives if not outcome.detected}),
    }


def cost_per_1000(latency_ms: float, watts: float, price_per_kwh: float) -> float:
    """Electricity for 1,000 checks: seconds * watts = joules, / 3.6e6 = kWh."""
    joules = (latency_ms / 1000.0) * watts
    kwh = joules * 1000 / 3_600_000
    return round(kwh * price_per_kwh, 6)


def markdown(results: Sequence[dict], meta: dict) -> str:
    lines = [
        f"Measured {meta['timestamp']} on {meta['host']} ({meta['cpu']}, {meta['python']}), "
        f"model `{meta['model']}`.",
        "",
        "| Backend | Detection | False positives | p50 per check | p95 per check | Mean | Cost / 1,000 checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        detection = f"{result['detected']}/{result['positives']} ({result['detection_rate']:.0%})"
        fps = f"{result['false_positives']}/{result['negatives']}"
        cost = result.get("cost_per_1000")
        cost_cell = f"${cost:.6f}".rstrip("0").rstrip(".") if cost not in (None, 0) else ("$0" if cost == 0 else "—")
        lines.append(
            f"| {result['backend']} | {detection} | {fps} | {result['p50_ms']} ms | "
            f"{result['p95_ms']} ms | {result['mean_ms']} ms | {cost_cell} |"
        )
    lines.append("")
    lines.append(
        f"Cost is electricity only ({meta['watts']:.0f} W at ${meta['price_per_kwh']:.2f}/kWh); "
        "hardware amortization is excluded. Latency includes the local rules pass and one batched "
        "Kev request per endpoint."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="examples/sentinel.yaml")
    parser.add_argument("--endpoint", action="append", help="limit to one or more endpoints")
    parser.add_argument("--repeats", type=int, default=1, help="times to repeat each case (default 1)")
    parser.add_argument("--judge-cmd", help="external judge command for a cloud baseline")
    parser.add_argument("--no-kev", action="store_true", help="rules-only measurement")
    parser.add_argument("--watts", type=float, default=60.0, help="machine power draw for the cost estimate")
    parser.add_argument("--price-per-kwh", type=float, default=0.30)
    parser.add_argument("--json", dest="json_path", help="write raw results as JSON")
    parser.add_argument("--markdown", dest="markdown_path", help="write a markdown table")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    endpoints = [config.endpoint(name) for name in args.endpoint] if args.endpoint else list(config.endpoints)
    cases = case_set(config, endpoints)
    if not cases:
        print("no cases to run", file=sys.stderr)
        return 2

    judges = []
    if not args.no_kev:
        judges.append(("kev + rules", KevClient(config.sem)))
    if args.judge_cmd:
        judges.append((Path(args.judge_cmd).name, SubprocessJudge(args.judge_cmd)))
    backends = [("rules only", None)] + judges

    if judges:
        # one warm request per judge: first-call graph compilation and prefix-cache
        # misses are not what a per-check latency should report
        for _, judge in judges:
            try:
                run_case(config, cases[0], judge=judge, use_kev=True)
            except KevError as exc:
                print(f"warmup failed: {exc}", file=sys.stderr)
                return 3

    results: list[dict] = []
    all_outcomes: list[dict] = []
    for label, judge in backends:
        outcomes: list[tuple[Case, Outcome]] = []
        for case in cases:
            for _ in range(max(1, args.repeats)):
                try:
                    outcome = run_case(config, case, judge=judge, use_kev=judge is not None)
                except KevError as exc:
                    print(f"{label}: {exc}", file=sys.stderr)
                    return 3
                outcomes.append((case, outcome))
        summary = summarize(outcomes, label)
        summary["cost_per_1000"] = (
            cost_per_1000(summary["mean_ms"], args.watts, args.price_per_kwh)
            if judge is None or not args.judge_cmd
            else None
        )
        results.append(summary)
        for case, outcome in outcomes:
            all_outcomes.append(
                {
                    "backend": label,
                    "endpoint": case.endpoint.name,
                    "mutation": case.mutation,
                    "label": case.label,
                    "detected": outcome.detected,
                }
            )
        print(
            f"{label}: {summary['detected']}/{summary['positives']} detected, "
            f"p50 {summary['p50_ms']} ms, missed {summary['missed'] or 'none'}",
            file=sys.stderr,
        )

    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": platform.platform(),
        "cpu": platform.processor() or "unknown",
        "python": platform.python_version(),
        "model": config.sem.model,
        "kev_base_url": config.sem.base_url,
        "watts": args.watts,
        "price_per_kwh": args.price_per_kwh,
        "cases": len(cases),
        "repeat": args.repeats,
    }
    document = {"meta": meta, "results": results, "outcomes": all_outcomes}
    table = markdown(results, meta)
    print(table)
    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2), encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    if args.markdown_path:
        target = Path(args.markdown_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(table + "\n", encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
