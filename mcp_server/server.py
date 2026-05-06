"""
MCP Server — exposes the JUnit Generator Pipeline as MCP tools.
Clients (Claude Desktop, other agents) can invoke the pipeline via MCP protocol.

Run with:
    python -m mcp_server.server
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from graph import build_pipeline, PipelineState
from config.settings import settings

mcp_server = FastMCP(
    "junit-generator-pipeline",
    description="Automated JUnit test generation pipeline for Spring Boot projects",
)

# In-memory store for active pipeline runs (thread_id → graph instance)
_active_runs: dict[str, Any] = {}


@mcp_server.tool()
def run_pipeline(
    project_path: str = "",
    bitbucket_repo_url: str = "",
    coverage_threshold: float = 80.0,
    pass_threshold: float = 80.0,
    max_coverage_iterations: int = 5,
    max_test_pass_iterations: int = 5,
    maven_cmd: str = "mvn",
) -> dict[str, Any]:
    """
    Start the JUnit Generator Pipeline for a Spring Boot project.

    Args:
        project_path: Local directory path to the Maven project.
        bitbucket_repo_url: Bitbucket HTTPS clone URL (alternative to project_path).
        coverage_threshold: Minimum JaCoCo coverage % required (default 80).
        pass_threshold: Minimum test pass rate % required (default 80).
        max_coverage_iterations: Max retries for coverage improvement (default 5).
        max_test_pass_iterations: Max retries for test pass improvement (default 5).
        maven_cmd: Maven executable path (default 'mvn').

    Returns:
        Pipeline result or human-in-the-loop pause notification.
    """
    if not project_path and not bitbucket_repo_url:
        return {"error": "Provide either project_path or bitbucket_repo_url"}

    input_source = "bitbucket" if bitbucket_repo_url else "directory"

    initial_state: PipelineState = {
        "project_path": project_path,
        "input_source": input_source,
        "bitbucket_repo_url": bitbucket_repo_url,
        "coverage_threshold": coverage_threshold,
        "pass_threshold": pass_threshold,
        "max_coverage_iterations": max_coverage_iterations,
        "max_test_pass_iterations": max_test_pass_iterations,
        "maven_cmd": maven_cmd,
    }

    pipeline = build_pipeline()
    thread_id = f"run-{id(pipeline)}"
    config = {"configurable": {"thread_id": thread_id}}

    # Run until interrupt or completion
    result = None
    for event in pipeline.stream(initial_state, config=config, stream_mode="values"):
        result = event

    if result is None:
        return {"error": "Pipeline produced no output"}

    stage = result.get("stage", "")

    # Check if paused for human review
    if "human_review_pending" in stage:
        _active_runs[thread_id] = pipeline
        return {
            "status": "paused_for_human_review",
            "thread_id": thread_id,
            "stage": stage,
            "reason": result.get("human_loop_reason", ""),
            "compilation_report": result.get("compilation_report", []),
            "coverage_data": result.get("coverage_data", {}),
            "execution_report": result.get("execution_report", {}),
            "message": (
                f"Pipeline paused. Use 'resume_pipeline' tool with thread_id='{thread_id}' "
                "after manual fixes are applied."
            ),
        }

    if stage == "done":
        return {
            "status": "completed",
            "thread_id": thread_id,
            "final_report": result.get("final_report", {}),
        }

    return {
        "status": "error",
        "stage": stage,
        "error": result.get("error", "Unknown error"),
    }


@mcp_server.tool()
def resume_pipeline(
    thread_id: str,
    approved: bool = True,
    feedback: str = "",
) -> dict[str, Any]:
    """
    Resume a paused pipeline after human review.

    Args:
        thread_id: The thread_id returned by run_pipeline when it paused.
        approved: True to continue pipeline, False to abort.
        feedback: Optional human feedback/notes to include in state.

    Returns:
        Resumed pipeline result.
    """
    pipeline = _active_runs.get(thread_id)
    if not pipeline:
        return {"error": f"No active run found for thread_id: {thread_id}"}

    config = {"configurable": {"thread_id": thread_id}}

    # Inject human decision into state
    pipeline.update_state(
        config,
        {
            "human_approved": approved,
            "human_feedback": feedback,
            "stage": "human_reviewed",
        },
    )

    if not approved:
        del _active_runs[thread_id]
        return {"status": "aborted", "message": "Pipeline aborted by human reviewer."}

    # Resume execution
    result = None
    for event in pipeline.stream(None, config=config, stream_mode="values"):
        result = event

    del _active_runs[thread_id]

    if result and result.get("stage") == "done":
        return {
            "status": "completed",
            "final_report": result.get("final_report", {}),
        }

    # May have paused again
    stage = result.get("stage", "") if result else ""
    if "human_review_pending" in stage:
        new_thread_id = f"{thread_id}-resumed"
        _active_runs[new_thread_id] = pipeline
        return {
            "status": "paused_for_human_review",
            "thread_id": new_thread_id,
            "reason": result.get("human_loop_reason", ""),
        }

    return {"status": "completed_with_issues", "state": result}


@mcp_server.tool()
def get_pipeline_status(thread_id: str) -> dict[str, Any]:
    """Get the current status of a pipeline run."""
    if thread_id in _active_runs:
        pipeline = _active_runs[thread_id]
        config = {"configurable": {"thread_id": thread_id}}
        state = pipeline.get_state(config)
        return {
            "status": "active",
            "thread_id": thread_id,
            "stage": state.values.get("stage", "unknown"),
            "coverage_iterations": state.values.get("coverage_iterations", 0),
            "test_pass_iterations": state.values.get("test_pass_iterations", 0),
        }
    return {"status": "not_found", "thread_id": thread_id}


if __name__ == "__main__":
    mcp_server.run(transport="stdio")
