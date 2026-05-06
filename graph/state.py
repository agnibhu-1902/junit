"""
LangGraph state definition for the JUnit Generator Pipeline.
All agents read from and write to this shared state object.
"""
from __future__ import annotations

from typing import Any, Literal
from typing_extensions import TypedDict


class PipelineState(TypedDict, total=False):
    # ── Input ──────────────────────────────────────────────────────────────
    project_path: str                    # Local path to the Maven project
    input_source: str                    # "directory" | "bitbucket"
    bitbucket_repo_url: str              # Bitbucket repo URL (if applicable)
    maven_cmd: str                       # Maven executable (default: "mvn")

    # ── Thresholds ─────────────────────────────────────────────────────────
    coverage_threshold: float            # e.g. 80.0
    pass_threshold: float                # e.g. 80.0
    max_coverage_iterations: int
    max_test_pass_iterations: int

    # ── Generator ──────────────────────────────────────────────────────────
    generated_test_files: list[str]
    generation_errors: list[str]
    fine_tune_type: str                  # "" | "coverage" | "failures"

    # ── Validator ──────────────────────────────────────────────────────────
    validation_results: list[dict]
    validation_fixed_count: int
    validation_invalid_count: int

    # ── Compilation ────────────────────────────────────────────────────────
    compilation_success: bool
    compilation_report: list[dict]
    compilation_fixed_count: int
    compilation_unfixed_count: int
    compilation_remaining_errors: list[dict]

    # ── JaCoCo ─────────────────────────────────────────────────────────────
    coverage_data: dict[str, Any]
    coverage_iterations: int
    coverage_met: bool

    # ── Test Executor ──────────────────────────────────────────────────────
    execution_report: dict[str, Any]
    failure_data: dict[str, Any]
    test_pass_iterations: int
    tests_passed: bool

    # ── Routing ────────────────────────────────────────────────────────────
    stage: str
    next: str                            # Next node hint
    error: str

    # ── Human-in-the-Loop ──────────────────────────────────────────────────
    human_loop_reason: str
    human_feedback: str                  # Injected by human reviewer
    human_approved: bool
