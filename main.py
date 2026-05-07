"""
JUnit Generator Pipeline — CLI Entry Point

Usage:
    # Run against a local directory
    python main.py --project-path /path/to/springboot-project

    # Run against a Bitbucket repo
    python main.py --bitbucket-url https://bitbucket.org/workspace/repo.git

    # Custom thresholds
    python main.py --project-path /path/to/project --coverage 85 --pass-rate 90

    # JSON output (for UI integration)
    python main.py --project-path /path/to/project --json

    # Start MCP server (for Claude Desktop / other MCP clients)
    python main.py --serve
"""
from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.rule import Rule

from graph import build_pipeline, PipelineState
from config.settings import settings

console = Console()

# Stages that mean the graph interrupted for human review
_HUMAN_LOOP_STAGES = {
    "compilation_failed",
    "coverage_failed_max_iterations",
    "tests_failed_max_iterations",
}


def main(
    project_path: Optional[str] = typer.Option(
        None, "--project-path", "-p", help="Local path to Maven Spring Boot project"
    ),
    bitbucket_url: Optional[str] = typer.Option(
        None, "--bitbucket-url", "-b", help="Bitbucket HTTPS clone URL"
    ),
    coverage_threshold: float = typer.Option(
        settings.COVERAGE_THRESHOLD, "--coverage", "-c", help="Minimum JaCoCo coverage %"
    ),
    pass_threshold: float = typer.Option(
        settings.TEST_PASS_THRESHOLD, "--pass-rate", "-r", help="Minimum test pass rate %"
    ),
    max_coverage_iter: int = typer.Option(
        settings.MAX_COVERAGE_ITERATIONS, "--max-coverage-iter", help="Max coverage retry iterations"
    ),
    max_pass_iter: int = typer.Option(
        settings.MAX_TEST_PASS_ITERATIONS, "--max-pass-iter", help="Max test pass retry iterations"
    ),
    maven_cmd: str = typer.Option(
        settings.MAVEN_CMD, "--maven", "-m", help="Maven executable"
    ),
    output_json: bool = typer.Option(
        False, "--json", "-j", help="Output final report as JSON"
    ),
    serve: bool = typer.Option(
        False, "--serve", "-s", help="Start the MCP server instead of running the pipeline"
    ),
):
    """Automated JUnit test generation pipeline for Spring Boot projects."""

    # ── MCP server mode ────────────────────────────────────────────────────
    if serve:
        from mcp_server.server import mcp_server
        console.print("[cyan]Starting JUnit Generator Pipeline MCP Server...[/cyan]")
        mcp_server.run(transport="stdio")
        return

    # ── Validate input ─────────────────────────────────────────────────────
    if not project_path and not bitbucket_url:
        console.print("[red]Error:[/red] Provide --project-path or --bitbucket-url")
        raise typer.Exit(1)

    input_source = "bitbucket" if bitbucket_url else "directory"

    initial_state: PipelineState = {
        "project_path": project_path or "",
        "input_source": input_source,
        "bitbucket_repo_url": bitbucket_url or "",
        "coverage_threshold": coverage_threshold,
        "pass_threshold": pass_threshold,
        "max_coverage_iterations": max_coverage_iter,
        "max_test_pass_iterations": max_pass_iter,
        "maven_cmd": maven_cmd,
    }

    console.print(
        Panel.fit(
            f"[bold cyan]JUnit Generator Pipeline[/bold cyan]\n"
            f"Source:   [yellow]{bitbucket_url or project_path}[/yellow]\n"
            f"LLM:      [magenta]{settings.LLM_PROVIDER} / {settings.LLM_MODEL}[/magenta]\n"
            f"Coverage: [green]{coverage_threshold}%[/green]  "
            f"Pass rate: [green]{pass_threshold}%[/green]",
            border_style="cyan",
        )
    )

    pipeline = build_pipeline()
    thread_id = "cli-run-001"
    config = {"configurable": {"thread_id": thread_id}}

    current_state = _run_pipeline_stream(pipeline, initial_state, config)

    if current_state is None:
        console.print("[red]Pipeline produced no output.[/red]")
        raise typer.Exit(1)

    # ── Human-in-the-loop loop ─────────────────────────────────────────────
    # LangGraph interrupts BEFORE human_loop node, so the last stage is the
    # failure stage (e.g. "compilation_failed"), not "human_review_pending".
    # We detect this by checking the graph's next pending tasks.
    while _is_interrupted(pipeline, config, current_state):
        _handle_human_review(pipeline, config, current_state, console)
        current_state = _run_pipeline_stream(pipeline, None, config)
        if current_state is None:
            break

    # ── Output ─────────────────────────────────────────────────────────────
    if output_json:
        report = current_state.get("final_report") or current_state
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(current_state, console)


# ---------------------------------------------------------------------------
# Pipeline streaming helper
# ---------------------------------------------------------------------------

def _run_pipeline_stream(pipeline, initial_state, config: dict) -> dict | None:
    """Stream the pipeline and return the last state, showing stage progress."""
    current_state = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Running...", total=None)
        for event in pipeline.stream(initial_state, config=config, stream_mode="values"):
            current_state = event
            stage = event.get("stage", "")
            progress.update(task, description=f"Stage: [bold]{stage}[/bold]")
    return current_state


def _is_interrupted(pipeline, config: dict, state: dict) -> bool:
    """
    Check whether the graph is paused at the human_loop interrupt point.
    LangGraph sets next tasks to ['human_loop'] when interrupted before it.
    """
    stage = state.get("stage", "")
    # Check via graph state snapshot
    try:
        snapshot = pipeline.get_state(config)
        return "human_loop" in (snapshot.next or [])
    except Exception:
        # Fallback: check stage name
        return stage in _HUMAN_LOOP_STAGES


# ---------------------------------------------------------------------------
# Human-in-the-loop handler
# ---------------------------------------------------------------------------

def _handle_human_review(pipeline, config: dict, state: dict, console: Console) -> None:
    """Show the human review panel and inject the decision back into the graph."""
    stage = state.get("stage", "")
    reason = state.get("human_loop_reason", "Manual intervention required.")

    console.print()
    console.print(Rule("[bold yellow]⚠ Human Review Required[/bold yellow]", style="yellow"))
    console.print()

    # Show reason
    console.print(Panel(f"[red]{reason}[/red]", title="Reason", border_style="yellow"))

    # Show stage-specific details
    if "compilation" in stage:
        errors = state.get("compilation_remaining_errors", [])
        report = state.get("compilation_report", [])
        fixed = state.get("compilation_fixed_count", 0)
        unfixed = state.get("compilation_unfixed_count", 0)

        console.print(f"\n[bold]Compilation Summary:[/bold] {fixed} fixed, {unfixed} unfixed\n")

        if errors:
            console.print("[bold red]Remaining errors:[/bold red]")
            for e in errors[:15]:
                console.print(f"  [red]•[/red] {e.get('line', str(e))}")
        elif report:
            console.print("[bold]Fix attempts:[/bold]")
            for r in report[:10]:
                status = "[green]✓[/green]" if r.get("fixed") else "[red]✗[/red]"
                console.print(f"  {status} {r.get('file', 'unknown')}: {r.get('reason', '')}")

    elif "coverage" in stage:
        cov = state.get("coverage_data", {})
        iters = state.get("coverage_iterations", 0)
        console.print(
            f"\n[bold]Coverage:[/bold] {cov.get('coverage_percentage', 0):.1f}% "
            f"(target: {state.get('coverage_threshold', 80)}%, after {iters} iterations)\n"
        )
        low = cov.get("low_coverage_classes", [])
        if low:
            console.print("[bold]Lowest coverage classes:[/bold]")
            for c in low[:10]:
                console.print(f"  [red]•[/red] {c['class']}: {c['coverage_pct']}%")

    elif "tests" in stage:
        report = state.get("execution_report", {}).get("surefire", {})
        iters = state.get("test_pass_iterations", 0)
        console.print(
            f"\n[bold]Test Results:[/bold] "
            f"{report.get('passed', 0)}/{report.get('total', 0)} passed "
            f"({report.get('pass_rate', 0):.1f}%, after {iters} iterations)\n"
        )
        failed = report.get("failed_tests", [])
        if failed:
            console.print("[bold]Failing tests:[/bold]")
            for t in failed[:10]:
                console.print(f"  [red]•[/red] {t.get('classname')}.{t.get('name')}")
                if t.get("message"):
                    console.print(f"    [dim]{t['message'][:120]}[/dim]")

    console.print()
    approved = typer.confirm(
        "Apply fixes manually, then confirm to continue. Continue pipeline?",
        default=True,
    )
    feedback = ""
    if approved:
        feedback = typer.prompt("Optional notes (press Enter to skip)", default="", show_default=False)

    pipeline.update_state(
        config,
        {
            "human_approved": approved,
            "human_feedback": feedback,
            "stage": "human_reviewed",
        },
    )

    if not approved:
        console.print("[yellow]Pipeline aborted.[/yellow]")
        raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_report(state: dict, console: Console) -> None:
    """Print either the final success report or a mid-pipeline failure summary."""
    stage = state.get("stage", "")
    report = state.get("final_report")

    # ── Success path ───────────────────────────────────────────────────────
    if report and stage == "done":
        console.print()
        console.print(Panel.fit("[bold green]✓ Pipeline Complete[/bold green]", border_style="green"))

        table = Table(title="Pipeline Summary", show_header=True, header_style="bold cyan")
        table.add_column("Stage", style="bold")
        table.add_column("Result", justify="center")
        table.add_column("Details")

        gen_files = report.get("generated_test_files", [])
        table.add_row("Test Generation", "[green]✓[/green]", f"{len(gen_files)} test files generated")

        val = report.get("validation", {})
        table.add_row("Validation", "[green]✓[/green]",
                      f"{val.get('fixed', 0)} auto-fixed, {val.get('invalid', 0)} invalid")

        comp = report.get("compilation", {})
        table.add_row(
            "Compilation",
            "[green]✓[/green]" if comp.get("success") else "[red]✗[/red]",
            f"{comp.get('fixed', 0)} errors fixed, {comp.get('unfixed', 0)} unfixed",
        )

        cov = report.get("coverage", {})
        table.add_row(
            "Code Coverage",
            "[green]✓[/green]" if cov.get("met") else "[red]✗[/red]",
            f"{cov.get('percentage', 0):.1f}% (target {cov.get('threshold', 80)}%, "
            f"{cov.get('iterations', 0)} iteration(s))",
        )

        ex = report.get("test_execution", {})
        table.add_row(
            "Test Execution",
            "[green]✓[/green]" if ex.get("met") else "[red]✗[/red]",
            f"{ex.get('passed', 0)}/{ex.get('total', 0)} passed "
            f"({ex.get('pass_rate', 0):.1f}%, target {ex.get('threshold', 80)}%, "
            f"{ex.get('iterations', 0)} iteration(s))",
        )

        console.print(table)

        if gen_files:
            console.print()
            console.print("[bold]Generated Test Files:[/bold]")
            for f in gen_files:
                console.print(f"  [green]•[/green] {f}")
        return

    # ── Failure / mid-pipeline path ────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        f"[bold red]Pipeline stopped at stage: {stage}[/bold red]",
        border_style="red",
    ))

    # Show what did complete
    table = Table(title="Progress Summary", show_header=True, header_style="bold cyan")
    table.add_column("Stage", style="bold")
    table.add_column("Result", justify="center")
    table.add_column("Details")

    gen_files = state.get("generated_test_files", [])
    if gen_files:
        table.add_row("Test Generation", "[green]✓[/green]", f"{len(gen_files)} files")

    if state.get("validation_results") is not None:
        table.add_row("Validation", "[green]✓[/green]",
                      f"{state.get('validation_fixed_count', 0)} fixed")

    if "compilation" in stage or state.get("compilation_report"):
        fixed = state.get("compilation_fixed_count", 0)
        unfixed = state.get("compilation_unfixed_count", 0)
        table.add_row(
            "Compilation",
            "[red]✗[/red]",
            f"{fixed} fixed, [red]{unfixed} unfixed[/red]",
        )

        # Show the actual errors
        errors = state.get("compilation_remaining_errors", [])
        if errors:
            console.print(table)
            console.print()
            console.print("[bold red]Compilation errors that need manual fixing:[/bold red]")
            for e in errors[:20]:
                console.print(f"  [red]•[/red] {e.get('line', str(e))}")
            console.print()
            console.print(
                "[dim]Fix the errors above in your project, then re-run the pipeline.[/dim]"
            )
            return

    console.print(table)

    # Show error field if set
    if state.get("error"):
        console.print(f"\n[red]Error:[/red] {state['error']}")

    # Show LLM 403 hint
    if settings.LLM_PROVIDER == "grok":
        console.print(
            "\n[yellow]Tip:[/yellow] If you saw 403 errors above, your "
            "[cyan]GROK_API_KEY[/cyan] in [cyan].env[/cyan] may be invalid.\n"
            "Get a key at [link]https://console.x.ai[/link]"
        )


if __name__ == "__main__":
    typer.run(main)
