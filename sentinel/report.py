"""Terminal and markdown rendering. No business logic here."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .calibrate import CalibrationRow
from .chaos import ChaosResult
from .check import EndpointResult
from .core.store import RunRecord


def _tick(ok: bool) -> str:
    return "[green]✓[/]" if ok else "[red]✗[/]"


def render_check(console: Console, results: Sequence[EndpointResult], *, verbose: bool = False) -> None:
    for result in results:
        console.print(
            f"{_tick(result.ok)} [bold]{escape(result.endpoint)}[/]"
            f"  [dim]{result.status if result.status is not None else '—'}"
            f"  {result.duration_ms} ms  {result.source}[/]"
        )
        for finding in result.findings:
            if finding.severity == "info" and not verbose:
                continue
            if finding.severity == "warn":
                console.print(f"   [yellow]![/] [dim]warn  [/] {escape(finding.message)}")
            else:
                console.print(f"   {_tick(finding.ok)} [dim]rules [/] {escape(finding.message)}")
            for detail in finding.details:
                console.print(f"          [dim]{escape(detail)}[/]")
        for item in result.semantic:
            suffix = "" if item.passed else f"  [red]fails `{escape(item.fail_if)}`[/]"
            console.print(f"   {_tick(item.passed)} [dim]sem   [/] {escape(item.qid)}: {escape(item.observed)}{suffix}")
        if not result.semantic and result.semantic_skipped:
            console.print(f"   [dim]– sem   skipped: {escape(result.semantic_skipped)}[/]")
        if result.semantic_error:
            console.print(f"   [red]✗ sem   {escape(result.semantic_error)}[/]")
        if result.alert_sent:
            console.print("   [yellow]alert sent[/]")
    passed = sum(1 for result in results if result.ok)
    console.print(f"\n{passed}/{len(results)} endpoint(s) ok")


def check_markdown(results: Sequence[EndpointResult]) -> str:
    lines = [
        "| Endpoint | Result | Rules | Semantic | Detail |",
        "|---|---|---|---|---|",
    ]
    for result in results:
        rules = next((finding for finding in result.findings if finding.blocking), None)
        rules_cell = "fail: " + (rules.message if rules else "")
        if rules is None:
            rules_cell = "ok"
        failed_sem = [item for item in result.semantic if not item.passed]
        if result.semantic_error:
            sem_cell = "error: " + result.semantic_error
        elif result.semantic_skipped:
            sem_cell = "skipped"
        elif failed_sem:
            sem_cell = "fail: " + ", ".join(item.qid for item in failed_sem)
        else:
            sem_cell = "ok"
        detail = "; ".join(result.reasons) or "ok"
        detail = detail.replace("|", "\\|")
        lines.append(
            f"| {result.endpoint} | {'ok' if result.ok else 'FAIL'} | {rules_cell} | {sem_cell} | {detail} |"
        )
    return "\n".join(lines)


def render_chaos(console: Console, results: Sequence[ChaosResult], *, sem_enabled: bool = True) -> None:
    table = Table(title="chaos injection: which layer catches the change", show_lines=True)
    table.add_column("injector", style="bold", no_wrap=True)
    table.add_column("expected", no_wrap=True)
    table.add_column("rules", overflow="fold")
    table.add_column("Kev", overflow="fold")
    table.add_column("detected", justify="center", no_wrap=True)
    for result in results:
        if not result.applicable:
            table.add_row(
                escape(result.mutation),
                result.expected,
                "[dim]—[/]",
                "[dim]—[/]",
                "[dim]n/a[/]",
            )
            continue
        if result.rules_caught:
            rules_cell = "caught: " + (result.rules_reasons[0] if result.rules_reasons else "")
        else:
            rules_cell = "[dim]passed[/]"
        if result.sem_error:
            sem_cell = f"[red]error: {escape(result.sem_error)}[/]"
        elif result.sem_skipped:
            sem_cell = f"[dim]n/a ({escape(result.sem_skipped)})[/]"
        elif result.sem_caught:
            sem_cell = "caught: " + escape(result.sem_reasons[0] if result.sem_reasons else "")
        else:
            sem_cell = "[dim]passed[/]"
        table.add_row(
            escape(result.mutation),
            result.expected,
            rules_cell,
            sem_cell,
            "[green]yes[/]" if result.detected else "[red]NO[/]",
        )
    console.print(table)
    applicable = [
        result
        for result in results
        if result.applicable and (sem_enabled or result.expected != "semantics")
    ]
    detected = sum(1 for result in applicable if result.detected)
    sem_only = sum(1 for result in applicable if result.detected and not result.rules_caught)
    skipped = len(results) - len(applicable)
    console.print(
        f"{detected}/{len(applicable)} applicable mutations detected · {sem_only} caught by semantics only"
        + (f" · {skipped} not applicable" if skipped else "")
    )
    missed = [result.mutation for result in applicable if not result.detected]
    if missed:
        console.print(
            f"[yellow]not detected on this endpoint: {', '.join(missed)}[/] — "
            "add a question that covers it, or accept the gap"
        )


def chaos_markdown(results: Sequence[ChaosResult]) -> str:
    lines = ["| Injector | Expected | Rules | Kev | Detected |", "|---|---|---|---|---|"]
    for result in results:
        if not result.applicable:
            lines.append(f"| {result.mutation} | {result.expected} | — | — | n/a |")
            continue
        rules_cell = "caught" if result.rules_caught else "passed"
        if result.sem_error:
            sem_cell = "error"
        elif result.sem_skipped:
            sem_cell = "n/a"
        else:
            sem_cell = "caught" if result.sem_caught else "passed"
        lines.append(
            f"| {result.mutation} | {result.expected} | {rules_cell} | {sem_cell} | "
            f"{'yes' if result.detected else 'NO'} |"
        )
    return "\n".join(lines)


def render_history(console: Console, runs: Iterable[RunRecord]) -> None:
    table = Table(title="recent checks", show_lines=False)
    table.add_column("when", no_wrap=True)
    table.add_column("endpoint", no_wrap=True)
    table.add_column("result", no_wrap=True)
    table.add_column("detail", overflow="fold")
    rows = list(runs)
    if not rows:
        console.print("[dim]no runs recorded yet[/]")
        return
    for run in rows:
        summary = run.summary
        detail = "; ".join(summary.get("reasons") or []) or "ok"
        table.add_row(
            run.ts,
            escape(run.endpoint),
            "[green]ok[/]" if run.ok else "[red]FAIL[/]",
            escape(detail),
        )
    console.print(table)


def render_baselines(console: Console, entries: Sequence[dict[str, Any]]) -> None:
    table = Table(title="baselines")
    table.add_column("endpoint", no_wrap=True)
    table.add_column("set at", no_wrap=True)
    table.add_column("items")
    table.add_column("bytes")
    table.add_column("source")
    for entry in entries:
        table.add_row(
            escape(str(entry["endpoint"])),
            str(entry.get("set_at") or "—"),
            str(entry.get("item_count") if entry.get("item_count") is not None else "—"),
            str(entry.get("bytes") if entry.get("bytes") is not None else "—"),
            str(entry.get("source") or "—"),
        )
    console.print(table)


def render_doctor(console: Console, *, config_path: str, home: str, kev: dict | None, endpoints: Sequence[dict[str, Any]]) -> None:
    console.print(f"config  [bold]{escape(config_path)}[/]")
    console.print(f"data    [bold]{escape(home)}[/]")
    if kev is None:
        console.print("kev     [red]unreachable[/]")
    else:
        models = kev.get("data") if isinstance(kev.get("data"), list) else []
        names = ", ".join(str(model.get("id")) for model in models if isinstance(model, dict)) or "kev-latest"
        console.print(f"kev     [green]ok[/]  models: {escape(names)}")
    table = Table(title="endpoints")
    table.add_column("endpoint", no_wrap=True)
    table.add_column("baseline", no_wrap=True)
    table.add_column("age (d)", justify="right", no_wrap=True)
    table.add_column("questions")
    table.add_column("source")
    for entry in endpoints:
        table.add_row(
            escape(str(entry["endpoint"])),
            "[green]yes[/]" if entry["baseline"] else "[red]missing[/]",
            str(entry["questions"]),
            escape(str(entry["source"])),
        )
    console.print(table)


def render_preflight(console: Console, report) -> None:
    table = Table(title="preflight: is this config safe to schedule?", show_lines=False)
    table.add_column("endpoint", no_wrap=True)
    table.add_column("baseline", no_wrap=True)
    table.add_column("age (d)", justify="right", no_wrap=True)
    table.add_column("clean", no_wrap=True)
    table.add_column("proven", justify="right", no_wrap=True)
    table.add_column("weak", justify="right", no_wrap=True)
    table.add_column("unproven", justify="right", no_wrap=True)
    for item in report.endpoints:
        table.add_row(
            escape(item.endpoint),
            "[green]ok[/]" if item.baseline else "[red]missing[/]",
            f"[yellow]{item.baseline_age_days:g}[/]" if item.stale_baseline else (
                f"{item.baseline_age_days:g}" if item.baseline_age_days is not None else "—"
            ),
            "[green]ok[/]" if item.clean else "[red]FAIL[/]",
            str(len(item.proven_questions)),
            f"[yellow]{len(item.weak_questions)}[/]" if item.weak_questions else "0",
            str(len(item.unproven_questions)),
        )
    console.print(table)
    for item in report.endpoints:
        for qid, margin in item.weak_questions:
            console.print(f"  [yellow]weak[/] {escape(item.endpoint)}/{escape(qid)} margin {margin:.2f} < 0.15")
        if item.stale_baseline:
            console.print(
                f"  [yellow]stale baseline[/] {escape(item.endpoint)}: "
                f"{item.baseline_age_days:g} days old; re-baseline deliberately if the change was intended"
            )
        for qid in item.unproven_questions:
            console.print(f"  [dim]unproven[/] {escape(item.endpoint)}/{escape(qid)}: no matching injection")
    if report.missed_rules:
        console.print(f"  [red]rules missed:[/] {', '.join(report.missed_rules)}")
    if report.missed_semantics:
        console.print(f"  [red]semantics missed:[/] {', '.join(report.missed_semantics)}")
    if report.kev_unavailable:
        console.print("  [red]Kev was unreachable; semantic readiness is unknown[/]")
    for note in report.extra:
        console.print(f"  [dim]{escape(note)}[/]")
    if report.go:
        console.print("\n[green]GO[/] — baseline, clean check, thresholds and chaos coverage all hold")
    else:
        console.print("\n[red]NO-GO[/] — do not schedule this config until the failures above are resolved")


def history_markdown(runs: Sequence[RunRecord]) -> str:
    lines = ["| When | Endpoint | Result | Detail |", "|---|---|---|---|"]
    for run in runs:
        detail = "; ".join(run.summary.get("reasons") or []) or "ok"
        detail = detail.replace("|", "\\|")
        lines.append(f"| {run.ts} | {run.endpoint} | {'ok' if run.ok else 'FAIL'} | {detail} |")
    return "\n".join(lines)

def render_calibration(console: Console, rows: Sequence[CalibrationRow]) -> None:
    table = Table(title="calibration: clean vs injected drift", show_lines=True)
    table.add_column("endpoint", no_wrap=True)
    table.add_column("question", no_wrap=True)
    table.add_column("clean", justify="right", no_wrap=True)
    table.add_column("worst injection", no_wrap=True)
    table.add_column("suggested fail_if", no_wrap=True)
    table.add_column("margin", justify="right", no_wrap=True)
    for row in rows:
        suggested = row.suggested_fail_if or "[yellow]no signal[/]"
        margin = f"{row.separation:.2f}" if isinstance(row.separation, float) else "—"
        table.add_row(
            escape(row.endpoint),
            escape(row.question),
            escape(row.clean_observed.split("=")[-1]),
            escape(row.worst_label or "—"),
            suggested,
            margin,
        )
    console.print(table)


def calibration_markdown(rows: Sequence[CalibrationRow]) -> str:
    lines = [
        "| Endpoint | Question | Clean | Worst injection | Suggested fail_if |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.endpoint} | {row.question} | {row.clean_observed} | {row.note} | "
            f"{row.suggested_fail_if or 'no signal'} |"
        )
    return "\n".join(lines)
