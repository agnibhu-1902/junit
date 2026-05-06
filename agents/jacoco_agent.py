"""
JaCoCo Agent
Runs Maven JaCoCo plugin, parses the coverage report,
and decides whether to loop back to the generator or proceed.
"""
from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from config.prompts import JACOCO_ANALYSIS_SYSTEM, JACOCO_ANALYSIS_PROMPT
from config.settings import settings


class JacocoAgent(BaseAgent):
    """Generates and evaluates JaCoCo code coverage reports."""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        1. Run mvn jacoco:report
        2. Parse coverage percentage
        3. If >= threshold → proceed to test executor
        4. If < threshold and iterations < max → loop back to generator
        5. If < threshold and iterations >= max → human in the loop
        """
        project_path: str = state["project_path"]
        maven_cmd: str = state.get("maven_cmd", "mvn")
        coverage_iterations: int = state.get("coverage_iterations", 0)
        coverage_threshold: float = state.get("coverage_threshold", settings.COVERAGE_THRESHOLD)
        max_iterations: int = state.get("max_coverage_iterations", settings.MAX_COVERAGE_ITERATIONS)

        # Run jacoco report
        coverage_result = self.invoke_tool(
            "run_jacoco_report",
            project_path=project_path,
            maven_cmd=maven_cmd,
        )

        if not coverage_result.get("success"):
            # Try running tests first then jacoco
            coverage_result = self._run_tests_then_jacoco(project_path, maven_cmd)

        coverage_pct = coverage_result.get("coverage_percentage", 0.0)
        coverage_iterations += 1

        # LLM analysis for detailed insights
        analysis = self._analyze_coverage(
            project_path=project_path,
            coverage_result=coverage_result,
            coverage_threshold=coverage_threshold,
        )

        updated_coverage_data = {
            **coverage_result,
            "target": coverage_threshold,
            "analysis": analysis,
        }

        if coverage_pct >= coverage_threshold:
            return {
                **state,
                "coverage_data": updated_coverage_data,
                "coverage_iterations": coverage_iterations,
                "coverage_met": True,
                "stage": "coverage_passed",
                "next": "test_executor",
            }

        # Coverage below threshold
        if coverage_iterations >= max_iterations:
            return {
                **state,
                "coverage_data": updated_coverage_data,
                "coverage_iterations": coverage_iterations,
                "coverage_met": False,
                "stage": "coverage_failed_max_iterations",
                "next": "human_loop",
                "human_loop_reason": (
                    f"Code coverage {coverage_pct:.1f}% is below {coverage_threshold}% "
                    f"after {coverage_iterations} iterations. Manual intervention required."
                ),
            }

        # Loop back to generator with fine-tuned prompt
        return {
            **state,
            "coverage_data": updated_coverage_data,
            "coverage_iterations": coverage_iterations,
            "coverage_met": False,
            "fine_tune_type": "coverage",
            "stage": "coverage_below_threshold",
            "next": "junit_generator",
        }

    def _run_tests_then_jacoco(self, project_path: str, maven_cmd: str) -> dict[str, Any]:
        """Run mvn verify as fallback to generate jacoco data."""
        result = self.invoke_tool(
            "run_maven_verify",
            project_path=project_path,
            maven_cmd=maven_cmd,
        )
        return result.get("coverage", {"success": False, "coverage_percentage": 0.0})

    def _analyze_coverage(
        self,
        project_path: str,
        coverage_result: dict,
        coverage_threshold: float,
    ) -> dict[str, Any]:
        """Use LLM to analyze coverage gaps and provide recommendations."""
        coverage_pct = coverage_result.get("coverage_percentage", 0.0)
        low_coverage = coverage_result.get("low_coverage_classes", [])

        uncovered_details = "\n".join(
            f"  - {c['class']}: {c['coverage_pct']}% ({c['missed']} instructions missed)"
            for c in low_coverage[:20]  # Limit to top 20
        )

        user_prompt = JACOCO_ANALYSIS_PROMPT.format(
            project_path=project_path,
            current_coverage=coverage_pct,
            target_coverage=coverage_threshold,
            coverage_gap=max(0, coverage_threshold - coverage_pct),
            coverage_report=f"Overall: {coverage_pct}%",
            uncovered_details=uncovered_details or "No detailed data available",
        )

        return self.call_llm_json(JACOCO_ANALYSIS_SYSTEM, user_prompt)
