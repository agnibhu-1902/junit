"""
LangGraph node functions.
Each node wraps an agent and adapts it to the LangGraph node interface.
All nodes receive and return PipelineState.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import (
    JUnitGeneratorAgent,
    JUnitValidatorAgent,
    CompilationAgent,
    JacocoAgent,
    TestExecutorAgent,
)
from agents.base import get_llm, get_llm_text
from config.settings import settings
from graph.state import PipelineState
from tools.java_tools import (
    read_java_file,
    write_java_test_file,
    list_java_source_files,
    list_existing_test_files,
    read_test_file,
    run_maven_compile,
    run_maven_tests,
    run_jacoco_report,
    run_maven_verify,
    parse_surefire_reports,
    clone_bitbucket_repo,
    validate_project_structure,
)


# ---------------------------------------------------------------------------
# Shared tool registry (direct Python function calls via MCP tool wrappers)
# ---------------------------------------------------------------------------

TOOLS: dict[str, Any] = {
    "read_java_file": read_java_file,
    "write_java_test_file": write_java_test_file,
    "list_java_source_files": list_java_source_files,
    "list_existing_test_files": list_existing_test_files,
    "read_test_file": read_test_file,
    "run_maven_compile": run_maven_compile,
    "run_maven_tests": run_maven_tests,
    "run_jacoco_report": run_jacoco_report,
    "run_maven_verify": run_maven_verify,
    "parse_surefire_reports": parse_surefire_reports,
    "clone_bitbucket_repo": clone_bitbucket_repo,
    "validate_project_structure": validate_project_structure,
}


def _make_llm():
    """JSON-mode LLM — for agents that return structured JSON responses."""
    return get_llm(
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        base_url=settings.OLLAMA_BASE_URL,
        api_key=settings.OPENROUTER_API_KEY or settings.GROK_API_KEY,
    )


def _make_llm_text():
    """Text-mode LLM — for agents that generate free-form Java code."""
    return get_llm_text(
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        base_url=settings.OLLAMA_BASE_URL,
        api_key=settings.OPENROUTER_API_KEY or settings.GROK_API_KEY,
    )


# ---------------------------------------------------------------------------
# Node: Input Handler
# ---------------------------------------------------------------------------

def input_handler_node(state: PipelineState) -> PipelineState:
    """
    Validates input (directory path or Bitbucket URL).
    Clones repo if needed. Validates Maven project structure.
    """
    input_source = state.get("input_source", "directory")
    project_path = state.get("project_path", "")

    if input_source == "bitbucket":
        repo_url = state.get("bitbucket_repo_url", "")
        if not repo_url:
            return {**state, "error": "bitbucket_repo_url is required", "stage": "input_error"}

        # Derive clone directory from repo name
        repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
        clone_dir = str(Path(settings.CLONE_BASE_DIR) / repo_name)

        clone_result = TOOLS["clone_bitbucket_repo"](
            repo_url=repo_url,
            clone_dir=clone_dir,
            username=settings.BITBUCKET_USERNAME,
            app_password=settings.BITBUCKET_APP_PASSWORD,
        )

        if not clone_result["success"]:
            return {
                **state,
                "error": f"Failed to clone repo: {clone_result.get('error')}",
                "stage": "input_error",
            }

        project_path = clone_result["clone_path"]

    # Validate Maven project structure
    validation = TOOLS["validate_project_structure"](project_path=project_path)
    if not validation["success"]:
        return {
            **state,
            "error": f"Invalid Maven project: {validation['checks']}",
            "stage": "input_error",
        }

    return {
        **state,
        "project_path": project_path,
        "maven_cmd": state.get("maven_cmd", settings.MAVEN_CMD),
        "coverage_threshold": state.get("coverage_threshold", settings.COVERAGE_THRESHOLD),
        "pass_threshold": state.get("pass_threshold", settings.TEST_PASS_THRESHOLD),
        "max_coverage_iterations": state.get("max_coverage_iterations", settings.MAX_COVERAGE_ITERATIONS),
        "max_test_pass_iterations": state.get("max_test_pass_iterations", settings.MAX_TEST_PASS_ITERATIONS),
        "coverage_iterations": 0,
        "test_pass_iterations": 0,
        "fine_tune_type": "",
        "stage": "input_validated",
    }


# ---------------------------------------------------------------------------
# Node: JUnit Generator
# ---------------------------------------------------------------------------

def junit_generator_node(state: PipelineState) -> PipelineState:
    """Generate JUnit test files for all source files."""
    # Text mode: generator produces Java code, not JSON
    agent = JUnitGeneratorAgent(llm=_make_llm_text(), tools=TOOLS)
    return agent.run(state)


# ---------------------------------------------------------------------------
# Node: JUnit Validator
# ---------------------------------------------------------------------------

def junit_validator_node(state: PipelineState) -> PipelineState:
    """Validate and auto-fix generated test files."""
    agent = JUnitValidatorAgent(llm=_make_llm(), tools=TOOLS)
    return agent.run(state)


# ---------------------------------------------------------------------------
# Node: Compilation Agent
# ---------------------------------------------------------------------------

def compilation_agent_node(state: PipelineState) -> PipelineState:
    """Compile the project and auto-fix compilation errors."""
    # Text mode: compilation agent produces fixed Java code
    agent = CompilationAgent(llm=_make_llm_text(), tools=TOOLS)
    return agent.run(state)


# ---------------------------------------------------------------------------
# Node: JaCoCo Agent
# ---------------------------------------------------------------------------

def jacoco_agent_node(state: PipelineState) -> PipelineState:
    """Run JaCoCo and evaluate coverage."""
    agent = JacocoAgent(llm=_make_llm(), tools=TOOLS)
    return agent.run(state)


# ---------------------------------------------------------------------------
# Node: Test Executor
# ---------------------------------------------------------------------------

def test_executor_node(state: PipelineState) -> PipelineState:
    """Execute JUnit tests and evaluate pass rate."""
    agent = TestExecutorAgent(llm=_make_llm(), tools=TOOLS)
    return agent.run(state)


# ---------------------------------------------------------------------------
# Node: Human-in-the-Loop
# ---------------------------------------------------------------------------

def human_loop_node(state: PipelineState) -> PipelineState:
    """
    Pause point for human review.
    LangGraph will interrupt BEFORE this node (interrupt_before=["human_loop"]).
    The human provides feedback via state update before resuming.
    """
    reason = state.get("human_loop_reason", "Manual review required")
    stage = state.get("stage", "unknown")

    # This node just records the pause — actual human input is injected
    # via graph.update_state() before resuming.
    return {
        **state,
        "stage": f"human_review_pending:{stage}",
        "human_loop_reason": reason,
    }


# ---------------------------------------------------------------------------
# Node: Done
# ---------------------------------------------------------------------------

def done_node(state: PipelineState) -> PipelineState:
    """Terminal node — assembles the final pipeline report."""
    coverage_data = state.get("coverage_data", {})
    execution_report = state.get("execution_report", {})
    surefire = execution_report.get("surefire", {})

    final_report = {
        "status": "completed",
        "project_path": state.get("project_path"),
        "generated_test_files": state.get("generated_test_files", []),
        "validation": {
            "fixed": state.get("validation_fixed_count", 0),
            "invalid": state.get("validation_invalid_count", 0),
        },
        "compilation": {
            "success": state.get("compilation_success", False),
            "fixed": state.get("compilation_fixed_count", 0),
            "unfixed": state.get("compilation_unfixed_count", 0),
        },
        "coverage": {
            "percentage": coverage_data.get("coverage_percentage", 0.0),
            "threshold": state.get("coverage_threshold", 80.0),
            "iterations": state.get("coverage_iterations", 0),
            "met": state.get("coverage_met", False),
        },
        "test_execution": {
            "total": surefire.get("total", 0),
            "passed": surefire.get("passed", 0),
            "failed": surefire.get("failed", 0),
            "pass_rate": surefire.get("pass_rate", 0.0),
            "threshold": state.get("pass_threshold", 80.0),
            "iterations": state.get("test_pass_iterations", 0),
            "met": state.get("tests_passed", False),
        },
    }

    return {**state, "final_report": final_report, "stage": "done"}
