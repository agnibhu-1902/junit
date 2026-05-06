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
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from graph import build_pipeline, PipelineState
from config.settings import settings

console = Console()


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

    # ── Pipeline mode ──────────────────────────────────────────────────────
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
            f"Source: [yellow]{bitbucket_url or project_path}[/yellow]\n"
            f"Coverage threshold: [green]{coverage_threshold}%[/green]  "
            f"Pass rate threshold: [green]{pass_threshold}%[/green]",
            border_style="cyan",
        )
    )

    pipeline = build_pipeline()
    thread_id = "cli-run-001"
    config = {"configurable": {"thread_id": thread_id}}

    current_state = None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Starting pipeline...", total=None)

        for event in pipeline.stream(initial_state, config=config, stream_mode="values"):
            current_state = event
            stage = event.get("stage", "")
            progress.update(task, description=f"Stage: [bold]{stage}[/bold]")

    if current_state is None:
        console.print("[red]Pipeline produced no output.[/red]")
        raise typer.Exit(1)

    stage = current_state.get("stage", "")

    # Handle human-in-the-loop pause(s)
    while "human_review_pending" in stage:
        _handle_human_review(pipeline, config, current_state, console)

        # Resume after human fix
        for event in pipeline.stream(None, config=config, stream_mode="values"):
            current_state = event
            stage = event.get("stage", "")
            console.print(f"  → Stage: [bold]{stage}[/bold]")

        stage = current_state.get("stage", "")

    # Output
    if output_json:
        print(json.dumps(current_state.get("final_report", current_state), indent=2))
    else:
        _print_final_report(current_state, console)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handle_human_review(pipeline, config: dict, state: dict, console: Console) -> None:
    """Interactive human-in-the-loop prompt."""
    reason = state.get("human_loop_reason", "Manual review required")
    stage = state.get("stage", "")

    console.print()
    console.print(
        Panel(
            f"[bold yellow]⚠ Human Review Required[/bold yellow]\n\n"
            f"[red]{reason}[/red]\n\n"
            f"Stage: {stage}",
            border_style="yellow",
            title="Human-in-the-Loop",
        )
    )

    # Show relevant context
    if "compilation" in stage:
        errors = state.get("compilation_remaining_errors", [])
        if errors:
            console.print("[bold]Remaining compilation errors:[/bold]")
            for e in errors[:10]:
                console.print(f"  [red]•[/red] {e.get('line', e)}")

    elif "coverage" in stage:
        cov = state.get("coverage_data", {})
        console.print(
            f"[bold]Coverage:[/bold] {cov.get('coverage_percentage', 0):.1f}% "
            f"(target: {state.get('coverage_threshold', 80)}%)"
        )

    elif "tests" in stage:
        report = state.get("execution_report", {}).get("surefire", {})
        console.print(
            f"[bold]Test Results:[/bold] "
            f"{report.get('passed', 0)}/{report.get('total', 0)} passed "
            f"({report.get('pass_rate', 0):.1f}%)"
        )

    console.print()
    approved = typer.confirm("Have you applied the manual fixes? Continue pipeline?", default=True)
    feedback = ""
    if approved:
        feedback = typer.prompt("Optional feedback/notes", default="", show_default=False)

    pipeline.update_state(
        config,
        {
            "human_approved": approved,
            "human_feedback": feedback,
            "stage": "human_reviewed",
        },
    )

    if not approved:
        console.print("[yellow]Pipeline aborted by user.[/yellow]")
        raise typer.Exit(0)


def _print_final_report(state: dict, console: Console) -> None:
    """Pretty-print the final pipeline report."""
    report = state.get("final_report", {})
    if not report:
        console.print("[yellow]No final report available.[/yellow]")
        return

    console.print()
    console.print(Panel.fit("[bold green]✓ Pipeline Complete[/bold green]", border_style="green"))

    table = Table(title="Pipeline Summary", show_header=True, header_style="bold cyan")
    table.add_column("Stage", style="bold")
    table.add_column("Result")
    table.add_column("Details")

    gen_files = report.get("generated_test_files", [])
    table.add_row(
        "Test Generation",
        "[green]✓[/green]",
        f"{len(gen_files)} test files generated",
    )

    val = report.get("validation", {})
    table.add_row(
        "Validation",
        "[green]✓[/green]",
        f"{val.get('fixed', 0)} auto-fixed, {val.get('invalid', 0)} invalid",
    )

    comp = report.get("compilation", {})
    comp_status = "[green]✓[/green]" if comp.get("success") else "[red]✗[/red]"
    table.add_row(
        "Compilation",
        comp_status,
        f"{comp.get('fixed', 0)} errors fixed, {comp.get('unfixed', 0)} unfixed",
    )

    cov = report.get("coverage", {})
    cov_pct = cov.get("percentage", 0.0)
    cov_status = "[green]✓[/green]" if cov.get("met") else "[red]✗[/red]"
    table.add_row(
        "Code Coverage",
        cov_status,
        f"{cov_pct:.1f}% (target: {cov.get('threshold', 80)}%, "
        f"{cov.get('iterations', 0)} iteration(s))",
    )

    exec_data = report.get("test_execution", {})
    pass_rate = exec_data.get("pass_rate", 0.0)
    exec_status = "[green]✓[/green]" if exec_data.get("met") else "[red]✗[/red]"
    table.add_row(
        "Test Execution",
        exec_status,
        f"{exec_data.get('passed', 0)}/{exec_data.get('total', 0)} passed "
        f"({pass_rate:.1f}%, target: {exec_data.get('threshold', 80)}%, "
        f"{exec_data.get('iterations', 0)} iteration(s))",
    )

    console.print(table)

    if gen_files:
        console.print()
        console.print("[bold]Generated Test Files:[/bold]")
        for f in gen_files:
            console.print(f"  [green]•[/green] {f}")


if __name__ == "__main__":
    typer.run(main)
