"""
LangGraph Workflow Definition for the JUnit Generator Pipeline.

Graph topology:
  input_handler
      │
      ▼
  junit_generator ◄──────────────────────────────────────────────────────┐
      │                                                                   │
      ▼                                                                   │
  junit_validator                                                         │
      │                                                                   │
      ▼                                                                   │
  compilation_agent                                                       │
      │                                                                   │
      ├─── unfixed errors ──► human_loop                                  │
      │                                                                   │
      ▼                                                                   │
  jacoco_agent                                                            │
      │                                                                   │
      ├─── coverage < 80% (< 5 iters) ────────────────────────────────────┘
      │
      ├─── coverage < 80% (≥ 5 iters) ──► human_loop
      │
      ▼
  test_executor
      │
      ├─── pass rate < 80% (< 5 iters) ──► junit_generator (fine-tune)
      │
      ├─── pass rate < 80% (≥ 5 iters) ──► human_loop
      │
      ▼
  done
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from graph.state import PipelineState
from graph.nodes import (
    input_handler_node,
    junit_generator_node,
    junit_validator_node,
    compilation_agent_node,
    jacoco_agent_node,
    test_executor_node,
    human_loop_node,
    done_node,
)


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def route_after_compilation(state: PipelineState) -> str:
    """Route after compilation: pass → jacoco, fail → human_loop."""
    if state.get("compilation_success"):
        return "jacoco_agent"
    return "human_loop"


def route_after_jacoco(state: PipelineState) -> str:
    """Route after JaCoCo: met → executor, below+retry → generator, max → human."""
    next_node = state.get("next", "")
    if next_node == "test_executor":
        return "test_executor"
    if next_node == "junit_generator":
        return "junit_generator"
    return "human_loop"


def route_after_executor(state: PipelineState) -> str:
    """Route after test execution: passed → done, retry → generator, max → human."""
    next_node = state.get("next", "")
    if next_node == "done":
        return "done"
    if next_node == "junit_generator":
        return "junit_generator"
    return "human_loop"


def route_after_human_loop(state: PipelineState) -> str:
    """
    Route after human review.
    Uses the 'next' field set by the failing agent before the interrupt —
    that field is preserved in the checkpoint across the human pause.
    """
    if not state.get("human_approved"):
        return END

    # 'next' was set by the agent that triggered the human loop
    next_hint = state.get("next", "")

    if next_hint == "human_loop":
        # Agent wanted human loop — after approval, re-run compilation
        # (human has fixed the files manually)
        return "compilation_agent"

    # Coverage or test failures sent us here
    stage = state.get("stage", "")
    if "coverage" in stage:
        return "test_executor"
    if "tests" in stage:
        return "done"

    # Default: re-run compilation after human fix
    return "compilation_agent"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_pipeline(checkpointer=None) -> Any:
    """Build and compile the LangGraph pipeline."""
    builder = StateGraph(PipelineState)

    # Register nodes
    builder.add_node("input_handler", input_handler_node)
    builder.add_node("junit_generator", junit_generator_node)
    builder.add_node("junit_validator", junit_validator_node)
    builder.add_node("compilation_agent", compilation_agent_node)
    builder.add_node("jacoco_agent", jacoco_agent_node)
    builder.add_node("test_executor", test_executor_node)
    builder.add_node("human_loop", human_loop_node)
    builder.add_node("done", done_node)

    # Entry point
    builder.set_entry_point("input_handler")

    # Linear edges
    builder.add_edge("input_handler", "junit_generator")
    builder.add_edge("junit_generator", "junit_validator")
    builder.add_edge("junit_validator", "compilation_agent")

    # Conditional edges
    builder.add_conditional_edges(
        "compilation_agent",
        route_after_compilation,
        {
            "jacoco_agent": "jacoco_agent",
            "human_loop": "human_loop",
        },
    )

    builder.add_conditional_edges(
        "jacoco_agent",
        route_after_jacoco,
        {
            "test_executor": "test_executor",
            "junit_generator": "junit_generator",
            "human_loop": "human_loop",
        },
    )

    builder.add_conditional_edges(
        "test_executor",
        route_after_executor,
        {
            "done": "done",
            "junit_generator": "junit_generator",
            "human_loop": "human_loop",
        },
    )

    builder.add_conditional_edges(
        "human_loop",
        route_after_human_loop,
        {
            "compilation_agent": "compilation_agent",
            "jacoco_agent": "jacoco_agent",
            "test_executor": "test_executor",
            "done": "done",
            END: END,
        },
    )

    builder.add_edge("done", END)

    # Use MemorySaver for human-in-the-loop checkpointing
    memory = checkpointer or MemorySaver()
    return builder.compile(
        checkpointer=memory,
        interrupt_before=["human_loop"],  # Pause before human review
    )
