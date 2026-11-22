"""Command line interface for kev-sentinel.

Exit codes:

    0  all checks passed
    1  a check failed (or a chaos assertion was not detected)
    2  config, baseline, or usage problem
    3  Kev is unreachable
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from rich.console import Console
from rich.markup import escape

from . import __version__
from .calibrate import run_calibration
from .chaos import MUTATIONS, MUTATORS, run_chaos
from .check import EndpointResult, run_check
from .config import Config, EndpointConfig, load_config, starter_config
from .core.fetch import fetch_endpoint, synthetic_result
from .core.fingerprint import fingerprint_result
from .core.store import Store
from .errors import BaselineError, ConfigError, KevError, SentinelError
from .report import (
    calibration_markdown,
    check_markdown,
    chaos_markdown,
    history_markdown,
    render_baselines,
    render_calibration,
    render_chaos,
    render_check,
    render_doctor,
    render_history,
)
from .sem.client import KevClient, probe
from .suggest import render_suggestions, starter_questions, suggest_questions

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_KEV = 3


def _console() -> Console:
    return Console(highlight=False, emoji=False)


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="path to sentinel.yaml (default: search the cwd upwards)")


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="machine-readable output on stdout")


def _baseline_entries(config: Config, store: Store) -> list[dict[str, Any]]:
    entries = []
    for endpoint in config.endpoints:
        baseline = store.baseline(endpoint.name)
        entries.append(
            {
                "endpoint": endpoint.name,
                "set_at": store.baseline_set_at(endpoint.name),
                "item_count": baseline.item_count if baseline else None,
                "bytes": baseline.nbytes if baseline else None,
                "source": baseline.source if baseline else None,
            }
        )
    return entries


def _write_github_summary(console: Console, text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        console.print("[yellow]--github-summary given but GITHUB_STEP_SUMMARY is not set[/]")
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
    except OSError as exc:
        console.print(f"[yellow]could not write GITHUB_STEP_SUMMARY: {exc}[/]")


def _side_effects(config: Config) -> tuple[Store, KevClient | None]:
    store = Store.for_home(config.home)
    judge = KevClient(config.sem) if config.sem.enabled else None
    return store, judge


# -- commands ---------------------------------------------------------------


def _print_suggestions(console: Console, suggestions: Sequence) -> None:
    if not suggestions:
        return
    console.print("\nquestions inferred from the payload:")
    for item in suggestions:
        mark = "" if item.confidence == "high" else "  [yellow](verify)[/]"
        console.print(
            f"  [bold]{escape(item.qid)}[/] ({item.question.type})  "
            f"fail_if `{escape(item.question.fail_if.raw)}`{mark}"
        )
        console.print(f"    [dim]{escape(item.rationale)}[/]")


def cmd_init(args: argparse.Namespace) -> int:
    if not args.url and not args.fixture:
        raise ConfigError("init needs --url and/or --fixture")
    config_path = Path(args.config).expanduser() if args.config else Path.cwd() / "sentinel.yaml"
    if config_path.exists() and not args.force:
        raise ConfigError(f"{config_path} already exists; pass --force to overwrite it")

    fixture = Path(args.fixture).expanduser() if args.fixture else None
    if fixture is not None and not fixture.is_file():
        raise ConfigError(f"fixture not found: {fixture}")
    fixture_ref = None
    if fixture is not None:
        try:
            fixture_ref = str(fixture.resolve().relative_to(config_path.parent.resolve()))
        except ValueError:
            fixture_ref = str(fixture.resolve())

    console = _console()
    probe = EndpointConfig(
        name=args.name, questions={}, url=args.url, fixture=fixture, method="GET"
    )
    result = fetch_endpoint(probe, timeout_s=15.0, live=fixture is None)

    questions = None
    suggestions = []
    if args.suggest:
        questions, suggestions = starter_questions(
            result.json if result.ok else None, endpoint_name=args.name
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        starter_config(
            args.name,
            url=args.url,
            fixture=fixture,
            fixture_ref=fixture_ref,
            questions=questions,
        ),
        encoding="utf-8",
    )
    console.print(f"wrote [bold]{config_path}[/]")
    _print_suggestions(console, suggestions)

    if result.ok and result.json is None:
        console.print(
            "[yellow]the endpoint did not return JSON; add a question set by hand "
            "or point --fixture at a recorded JSON body[/]"
        )
    if not result.ok:
        console.print(f"[red]could not fetch a baseline:[/] {result.error}")
        console.print(f"fix the URL, then run `sentinel baseline set {args.name}`")
        return EXIT_CONFIG

    config = load_config(config_path)
    if args.no_baseline:
        console.print("skipped baseline; run `sentinel baseline set " + args.name + "` when ready")
        return EXIT_OK

    with Store.for_home(config.home) as store:
        snapshot = store.record_snapshot(args.name, result, fingerprint_result(result))
        store.set_baseline(args.name, snapshot.id)

    fingerprint = snapshot.fingerprint
    paths = len(fingerprint.get("paths", {}))
    console.print(
        f"baseline for [bold]{args.name}[/]: HTTP {snapshot.status}, "
        f"{fingerprint.get('item_count') if fingerprint.get('item_count') is not None else '—'} items, "
        f"{snapshot.nbytes} bytes, {paths} schema paths ({snapshot.source})"
    )
    console.print(f"data: [dim]{config.home / 'sentinel.db'}[/]")
    console.print("\nnext:")
    console.print(f"  sentinel calibrate {args.name}   # replace default thresholds with measured ones")
    console.print(f"  sentinel check {args.name}")
    console.print(f"  sentinel chaos --endpoint {args.name}")
    return EXIT_OK


def cmd_baseline_set(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    console = _console()
    with Store.for_home(config.home) as store:
        for name in args.endpoints:
            endpoint = config.endpoint(name)
            result = fetch_endpoint(
                endpoint,
                timeout_s=endpoint.timeout_s or config.default_timeout_s,
                live=args.live,
            )
            if not result.ok:
                raise ConfigError(f"could not fetch {name}: {result.error}")
            snapshot = store.record_snapshot(endpoint.name, result, fingerprint_result(result))
            store.set_baseline(endpoint.name, snapshot.id)
            console.print(
                f"baseline set for [bold]{endpoint.name}[/]: HTTP {snapshot.status}, "
                f"{snapshot.fingerprint.get('item_count') if snapshot.fingerprint.get('item_count') is not None else '—'} items, "
                f"{snapshot.nbytes} bytes ({snapshot.source})"
            )
    return EXIT_OK


def cmd_baseline_show(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    endpoint = config.endpoint(args.endpoint)
    with Store.for_home(config.home) as store:
        baseline = store.baseline(endpoint.name)
        set_at = store.baseline_set_at(endpoint.name)
    console = _console()
    if baseline is None:
        console.print(f"[red]no baseline for {endpoint.name}[/]")
        return EXIT_CONFIG
    fingerprint = baseline.fingerprint
    console.print(f"endpoint   [bold]{endpoint.name}[/]")
    console.print(f"set at     {set_at}")
    console.print(
        f"payload    HTTP {baseline.status}, {fingerprint.get('item_count')} items, "
        f"{baseline.nbytes} bytes, {len(fingerprint.get('paths', {}))} schema paths"
    )
    console.print(f"source     {baseline.source}")
    console.print(f"sha256     {baseline.sha256}")
    records_path = fingerprint.get("records_path")
    console.print(f"records    {records_path if records_path is not None else '—'}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    console = _console()
    store, judge = _side_effects(config)
    try:
        if args.endpoints:
            endpoints = [config.endpoint(name) for name in args.endpoints]
        else:
            endpoints = list(config.endpoints)
        if not args.no_sem and judge is None:
            console.print("[yellow]semantic checks disabled in config; running rules only[/]")
        results: list[EndpointResult] = []
        baseline_errors = False
        for endpoint in endpoints:
            try:
                result = run_check(
                    config,
                    endpoint,
                    store,
                    judge,
                    live=args.live,
                    run_sem=not args.no_sem,
                    alerts=not args.no_alert,
                )
            except BaselineError as exc:
                console.print(f"[red]✗ {endpoint.name}[/]  {exc}")
                baseline_errors = True
                continue
            results.append(result)

        if args.json:
            print(json.dumps([result.to_dict() for result in results], indent=2, ensure_ascii=False))
        elif not args.quiet:
            render_check(console, results, verbose=args.verbose)
        if args.github_summary:
            _write_github_summary(console, "### kev-sentinel\n\n" + check_markdown(results))
    finally:
        store.close()

    if baseline_errors:
        return EXIT_CONFIG
    if any(result.semantic_error for result in results):
        return EXIT_KEV
    if any(not result.ok for result in results):
        return EXIT_FAIL
    return EXIT_OK


def _parse_rename(pairs: Sequence[str]) -> dict[str, str]:
    rename: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ConfigError(f"--rename expects old=new, got {pair!r}")
        old, new = pair.split("=", 1)
        if not old or not new:
            raise ConfigError(f"--rename expects old=new, got {pair!r}")
        rename[old] = new
    return rename


def cmd_chaos(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    console = _console()
    judge = None if args.no_sem else KevClient(config.sem)
    if judge is not None and not config.sem.enabled:
        raise ConfigError("chaos needs semantic checks; enable `sem` in sentinel.yaml or pass --no-sem")
    store = Store.for_home(config.home)
    try:
        endpoints = [config.endpoint(name) for name in args.endpoint] if args.endpoint else list(config.endpoints)
        rename = _parse_rename(args.rename or [])
        names = args.inject or None
        all_results = []
        for endpoint in endpoints:
            try:
                results = run_chaos(
                    config,
                    endpoint,
                    store,
                    judge,
                    names=names,
                    live=args.live,
                    rename=rename,
                )
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            all_results.extend(results)
            if not args.json:
                render_chaos(console, results, sem_enabled=not args.no_sem)
        if args.json:
            print(json.dumps([result.to_dict() for result in all_results], indent=2, ensure_ascii=False))
        if args.github_summary:
            _write_github_summary(console, "### kev-sentinel chaos\n\n" + chaos_markdown(all_results))
    finally:
        store.close()

    if any(result.sem_error for result in all_results):
        return EXIT_KEV
    if args.assert_:
        tested = sorted({result.mutation for result in all_results})
        undetected = []
        for name in tested:
            candidates = [
                result
                for result in all_results
                if result.mutation == name
                and result.applicable
                and not (args.no_sem and result.expected == "semantics")
            ]
            if candidates and all(not result.detected for result in candidates):
                undetected.append(name)
        if undetected:
            console.print(f"[red]undetected by every layer and endpoint: {', '.join(undetected)}[/]")
            return EXIT_FAIL
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    """One command that proves the whole idea: clean pass, injected drift, who caught it."""
    config = load_config(args.config)
    console = _console()
    relevant = [config.endpoint(name) for name in args.endpoint] if args.endpoint else list(config.endpoints)
    judge = None if args.no_sem else KevClient(config.sem)
    store = Store.for_home(config.home)
    try:
        console.print("[bold]1. baseline[/]")
        for endpoint in relevant:
            if store.baseline(endpoint.name) is not None:
                continue
            result = fetch_endpoint(
                endpoint,
                timeout_s=endpoint.timeout_s or config.default_timeout_s,
                live=args.live,
            )
            if not result.ok:
                console.print(f"   [red]{endpoint.name}: {escape(result.error or 'fetch failed')}[/]")
                return EXIT_CONFIG
            snapshot = store.record_snapshot(endpoint.name, result, fingerprint_result(result))
            store.set_baseline(endpoint.name, snapshot.id)
            console.print(f"   recorded {endpoint.name} ({snapshot.nbytes} bytes)")

        console.print("\n[bold]2. check the clean payloads[/]")
        results = []
        for endpoint in relevant:
            try:
                results.append(
                    run_check(
                        config,
                        endpoint,
                        store,
                        judge,
                        live=args.live,
                        run_sem=not args.no_sem,
                        alerts=False,
                    )
                )
            except BaselineError:
                continue
        render_check(console, results)
        clean_ok = bool(results) and all(result.ok for result in results)

        console.print("\n[bold]3. inject a silent change[/]")
        if args.no_sem:
            candidates = ["field-rename", "empty-list", "html-error"]
        elif args.inject:
            candidates = [args.inject]
        else:
            candidates = ["status-flip", "currency-swap", "unit-swap", "stale-dates", "wrong-entity"]

        preferred = [endpoint for endpoint in relevant if endpoint.name == "status-page"]
        ordered = preferred + [endpoint for endpoint in relevant if endpoint not in preferred]
        chosen_endpoint = None
        chosen_mutation = None
        for mutation_name in candidates:
            mutator = MUTATORS[mutation_name]
            for endpoint in ordered:
                base = fetch_endpoint(
                    endpoint,
                    timeout_s=endpoint.timeout_s or config.default_timeout_s,
                    live=args.live,
                )
                if not base.ok or base.json is None:
                    continue
                mutation = mutator.apply(base, {}, mutation_name)
                mutated = synthetic_result(
                    endpoint.name,
                    body=mutation.body,
                    status=mutation.status,
                    content_type=mutation.content_type,
                )
                changed = (
                    mutation.status != base.status
                    or mutation.content_type != base.content_type
                    or mutated.json != base.json
                )
                if changed:
                    chosen_endpoint, chosen_mutation = endpoint, mutation_name
                    break
            if chosen_endpoint is not None:
                break
        if chosen_endpoint is None or chosen_mutation is None:
            console.print("[red]no injector applies to these payloads; pass --inject[/]")
            return EXIT_FAIL

        chaos_results = run_chaos(
            config,
            chosen_endpoint,
            store,
            judge,
            names=[chosen_mutation],
            live=args.live,
        )
        render_chaos(console, chaos_results, sem_enabled=not args.no_sem)
        result = chaos_results[0]
        if not result.applicable:
            console.print("[red]the chosen injector did not change the payload; pass --inject[/]")
            return EXIT_FAIL
        caught_by_kev = result.sem_caught
        caught_by_rules = result.rules_caught

        console.print("")
        if clean_ok and (caught_by_kev or caught_by_rules):
            if caught_by_kev and not caught_by_rules:
                reason = result.sem_reasons[0] if result.sem_reasons else "a semantic question failed"
                console.print(
                    f"[green]verdict:[/] schema unchanged; Kev caught it ({escape(reason)})."
                )
            else:
                reason = result.rules_reasons[0] if result.rules_reasons else "a rule failed"
                console.print(f"[green]verdict:[/] the rules layer caught it ({escape(reason)}).")
            return EXIT_OK
        console.print("[red]verdict: the demo did not pass; inspect the tables above[/]")
        return EXIT_FAIL
    finally:
        store.close()


def cmd_calibrate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    console = _console()
    if not config.sem.enabled:
        raise ConfigError("calibration needs semantic checks; enable `sem` in sentinel.yaml")
    judge = KevClient(config.sem)
    endpoints = [config.endpoint(name) for name in args.endpoints] if args.endpoints else list(config.endpoints)
    all_rows = []
    with Store.for_home(config.home) as store:
        for endpoint in endpoints:
            try:
                rows = run_calibration(
                    config,
                    endpoint,
                    store,
                    judge,
                    live=args.live,
                    mutation_names=args.inject or None,
                )
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            all_rows.extend(rows)
            if not args.json:
                render_calibration(console, rows)
    if args.json:
        print(json.dumps([row.to_dict() for row in all_rows], indent=2, ensure_ascii=False))
    if args.github_summary:
        _write_github_summary(console, "### kev-sentinel calibration\n\n" + calibration_markdown(all_rows))
    missing = [row for row in all_rows if row.suggested_fail_if is None]
    if missing and not args.json:
        console.print(
            f"\n[yellow]{len(missing)} question(s) have no separation on these injections; "
            "rephrase the question or pick a different signal[/]"
        )
    return EXIT_OK


def cmd_suggest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    endpoint = config.endpoint(args.endpoint)
    console = _console()
    result = fetch_endpoint(
        endpoint,
        timeout_s=endpoint.timeout_s or config.default_timeout_s,
        live=args.live,
    )
    payload = result.json if result.ok else None
    if payload is None:
        with Store.for_home(config.home) as store:
            latest = store.latest_snapshot(endpoint.name)
        if latest is None or not latest.body:
            raise ConfigError(f"cannot read a payload for {endpoint.name}: {result.error or result.json_error}")
        try:
            payload = json.loads(latest.body)
        except ValueError as exc:
            raise ConfigError(f"latest snapshot for {endpoint.name} is not JSON: {exc}") from exc
        console.print(f"[dim]using the latest recorded snapshot from {latest.ts}[/]")
    suggestions = suggest_questions(payload, endpoint_name=endpoint.name)
    if args.json:
        print(json.dumps([item.to_dict() for item in suggestions], indent=2, ensure_ascii=False))
        return EXIT_OK
    if not suggestions:
        console.print(
            "[yellow]no high-confidence suggestions for this payload shape; "
            "keep the current questions and add one by hand[/]"
        )
        return EXIT_OK
    _print_suggestions(console, suggestions)
    console.print("\ncopy into sentinel.yaml:")
    console.print(escape(render_suggestions(suggestions)), markup=False)
    console.print(
        "\n[yellow]defaults, not calibrated:[/] run `sentinel calibrate "
        f"{endpoint.name}` and keep the thresholds with margin."
    )
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    console = _console()
    with Store.for_home(config.home) as store:
        runs = store.recent_runs(args.last)
        baselines = _baseline_entries(config, store)
    if args.json:
        payload = {
            "runs": [
                {
                    "endpoint": run.endpoint,
                    "ts": run.ts,
                    "ok": run.ok,
                    "reasons": run.summary.get("reasons") or [],
                }
                for run in runs
            ],
            "baselines": baselines,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    if args.md:
        lines = ["## Recent checks", "", history_markdown(runs), "", "## Baselines", ""]
        lines.append("| Endpoint | Set at | Items | Bytes |")
        lines.append("|---|---|---|---|")
        for entry in baselines:
            lines.append(
                f"| {entry['endpoint']} | {entry['set_at'] or '—'} | "
                f"{entry['item_count'] if entry['item_count'] is not None else '—'} | "
                f"{entry['bytes'] if entry['bytes'] is not None else '—'} |"
            )
        print("\n".join(lines))
        return EXIT_OK
    render_history(console, runs)
    render_baselines(console, baselines)
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    console = _console()
    config = load_config(args.config)
    kev: dict | None = None
    kev_error: str | None = None
    try:
        kev = probe(config.sem)
    except KevError as exc:
        kev_error = str(exc)
    with Store.for_home(config.home) as store:
        entries = []
        for endpoint in config.endpoints:
            baseline = store.baseline(endpoint.name)
            entries.append(
                {
                    "endpoint": endpoint.name,
                    "baseline": baseline is not None,
                    "questions": len(endpoint.questions),
                    "source": endpoint.fixture.name if endpoint.fixture else (endpoint.url or "—"),
                }
            )
    render_doctor(
        console,
        config_path=str(config.path),
        home=str(config.home),
        kev=kev,
        endpoints=entries,
    )
    if kev_error:
        console.print(f"[red]{escape(kev_error)}[/]")
        return EXIT_KEV
    if any(not entry["baseline"] for entry in entries):
        console.print("[yellow]some endpoints have no baseline; run `sentinel baseline set NAME`[/]")
        return EXIT_CONFIG
    return EXIT_OK


# -- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Semantic drift monitoring for APIs and scrapers, powered by a local Kev model.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write sentinel.yaml and record a baseline")
    init.add_argument("name", help="endpoint name, e.g. jobs-api")
    init.add_argument("--url", help="endpoint URL")
    init.add_argument("--fixture", help="recorded response body to use instead of (or with) a URL")
    init.add_argument("--force", action="store_true", help="overwrite an existing config")
    init.add_argument("--no-baseline", action="store_true", help="write the config but do not record a baseline")
    init.add_argument(
        "--no-suggest",
        dest="suggest",
        action="store_false",
        help="write the generic guard questions instead of inferring questions from the payload",
    )
    _add_config(init)
    init.set_defaults(func=cmd_init)

    baseline = sub.add_parser("baseline", help="manage recorded baselines")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    bset = baseline_sub.add_parser("set", help="fetch endpoints and record new baselines")
    bset.add_argument("endpoints", nargs="+")
    bset.add_argument("--live", action="store_true", help="fetch the URL even when a fixture is configured")
    _add_config(bset)
    bset.set_defaults(func=cmd_baseline_set)
    bshow = baseline_sub.add_parser("show", help="show the current baseline")
    bshow.add_argument("endpoint")
    _add_config(bshow)
    bshow.set_defaults(func=cmd_baseline_show)

    check = sub.add_parser("check", help="check endpoints against their baselines")
    check.add_argument("endpoints", nargs="*", help="endpoint names (default: all)")
    check.add_argument("--live", action="store_true", help="fetch URLs even when fixtures are configured")
    check.add_argument("--no-sem", action="store_true", help="run deterministic rules only")
    check.add_argument("--no-alert", action="store_true", help="do not send webhook alerts")
    check.add_argument("--quiet", action="store_true", help="print nothing except the exit code")
    check.add_argument("--verbose", action="store_true", help="include informational rule lines")
    check.add_argument("--github-summary", action="store_true", help="append markdown to $GITHUB_STEP_SUMMARY")
    _add_json(check)
    _add_config(check)
    check.set_defaults(func=cmd_check)

    chaos = sub.add_parser("chaos", help="inject silent changes and show which layer catches them")
    chaos.add_argument("--endpoint", action="append", help="endpoint name (repeatable; default: all)")
    chaos.add_argument("--inject", action="append", help=f"mutation name (repeatable): {', '.join(MUTATIONS)}")
    chaos.add_argument("--rename", action="append", help="field rename for field-rename, e.g. title=headline")
    chaos.add_argument("--live", action="store_true", help="fetch URLs even when fixtures are configured")
    chaos.add_argument("--no-sem", action="store_true", help="rules only; skips the Kev layer")
    chaos.add_argument("--assert", dest="assert_", action="store_true", help="exit 1 if any mutation goes undetected")
    chaos.add_argument("--github-summary", action="store_true", help="append markdown to $GITHUB_STEP_SUMMARY")
    _add_json(chaos)
    _add_config(chaos)
    chaos.set_defaults(func=cmd_chaos)

    demo = sub.add_parser("demo", help="one command: check clean, inject drift, show who caught it")
    demo.add_argument("--endpoint", action="append", help="endpoint name (repeatable; default: all)")
    demo.add_argument("--inject", help="mutation to inject (default: the first that applies)")
    demo.add_argument("--live", action="store_true", help="fetch URLs even when fixtures are configured")
    demo.add_argument("--no-sem", action="store_true", help="rules only; no Kev server required")
    _add_config(demo)
    demo.set_defaults(func=cmd_demo)

    suggest = sub.add_parser("suggest", help="infer questions from an endpoint's payload")
    suggest.add_argument("endpoint")
    suggest.add_argument("--live", action="store_true", help="fetch the URL even when a fixture is configured")
    _add_json(suggest)
    _add_config(suggest)
    suggest.set_defaults(func=cmd_suggest)

    calibrate = sub.add_parser("calibrate", help="measure clean vs injected drift and suggest thresholds")
    calibrate.add_argument("endpoints", nargs="*", help="endpoint names (default: all)")
    calibrate.add_argument("--inject", action="append", help="limit the injections used for calibration")
    calibrate.add_argument("--live", action="store_true", help="fetch URLs even when fixtures are configured")
    calibrate.add_argument("--github-summary", action="store_true", help="append markdown to $GITHUB_STEP_SUMMARY")
    _add_json(calibrate)
    _add_config(calibrate)
    calibrate.set_defaults(func=cmd_calibrate)

    report = sub.add_parser("report", help="show recent checks and baselines")
    report.add_argument("--last", type=int, default=20, help="number of runs (default: 20)")
    report.add_argument("--md", action="store_true", help="markdown output")
    _add_json(report)
    _add_config(report)
    report.set_defaults(func=cmd_report)

    doctor = sub.add_parser("doctor", help="check config, baselines, and the Kev server")
    _add_config(doctor)
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = _console()
    try:
        return int(args.func(args))
    except ConfigError as exc:
        console.print(f"[red]config error:[/] {exc}")
        return EXIT_CONFIG
    except KevError as exc:
        console.print(f"[red]kev error:[/] {exc}")
        return EXIT_KEV
    except SentinelError as exc:
        console.print(f"[red]error:[/] {exc}")
        return EXIT_CONFIG
    except KeyboardInterrupt:  # pragma: no cover
        console.print("[yellow]interrupted[/]")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())