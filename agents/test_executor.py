"""
JUnit Test Executor Agent
Runs all JUnit tests, parses Surefire reports,
and decides whether to loop back to the generator or return the final report.
"""
from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from config.prompts import EXECUTOR_ANALYSIS_SYSTEM, EXECUTOR_ANALYSIS_PROMPT
from config.settings import settings


class TestExecutorAgent(BaseAgent):
    """Executes JUnit tests and evaluates pass/fail rates."""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        1. Run mvn test
        2. Parse Surefire reports
        3. If pass rate >= threshold → return final report
        4. If pass rate < threshold and iterations < max → loop back to generator
        5. If pass rate < threshold and iterations >= max → human in the loop
        """
        project_path: str = state["project_path"]
        maven_cmd: str = state.get("maven_cmd", "mvn")
        test_pass_iterations: int = state.get("test_pass_iterations", 0)
        pass_threshold: float = state.get("pass_threshold", settings.TEST_PASS_THRESHOLD)
        max_iterations: int = state.get("max_test_pass_iterations", settings.MAX_TEST_PASS_ITERATIONS)

        # Run tests
        run_result = self.invoke_tool(
            "run_maven_tests",
            project_path=project_path,
            maven_cmd=maven_cmd,
        )

        # Parse detailed Surefire reports
        surefire_result = self.invoke_tool(
            "parse_surefire_reports",
            project_path=project_path,
        )

        test_pass_iterations += 1
        pass_rate = surefire_result.get("pass_rate", 0.0)
        failed_tests = surefire_result.get("failed_tests", [])

        # LLM analysis of failures
        failure_analysis = self._analyze_failures(
            surefire_result=surefire_result,
            pass_threshold=pass_threshold,
        )

        execution_report = {
            "run_result": run_result,
            "surefire": surefire_result,
            "analysis": failure_analysis,
            "pass_rate": pass_rate,
            "iteration": test_pass_iterations,
        }

        if pass_rate >= pass_threshold:
            return {
                **state,
                "execution_report": execution_report,
                "test_pass_iterations": test_pass_iterations,
                "tests_passed": True,
                "stage": "tests_passed",
                "next": "done",
            }

        # Below threshold
        if test_pass_iterations >= max_iterations:
            return {
                **state,
                "execution_report": execution_report,
                "failure_data": failure_analysis,
                "test_pass_iterations": test_pass_iterations,
                "tests_passed": False,
                "stage": "tests_failed_max_iterations",
                "next": "human_loop",
                "human_loop_reason": (
                    f"Test pass rate {pass_rate:.1f}% is below {pass_threshold}% "
                    f"after {test_pass_iterations} iterations. "
                    f"{len(failed_tests)} tests still failing. Manual intervention required."
                ),
            }

        # Loop back to generator with fine-tuned prompt
        return {
            **state,
            "execution_report": execution_report,
            "failure_data": failure_analysis,
            "test_pass_iterations": test_pass_iterations,
            "tests_passed": False,
            "fine_tune_type": "failures",
            "stage": "tests_below_threshold",
            "next": "junit_generator",
        }

    def _analyze_failures(
        self,
        surefire_result: dict,
        pass_threshold: float,
    ) -> dict[str, Any]:
        """Use LLM to analyze test failures and suggest fixes."""
        failed_tests = surefire_result.get("failed_tests", [])
        pass_rate = surefire_result.get("pass_rate", 0.0)

        if not failed_tests:
            return {
                "pass_rate": pass_rate,
                "meets_threshold": pass_rate >= pass_threshold,
                "failed_test_details": [],
                "summary": "All tests passed",
            }

        failed_details = "\n".join(
            f"  - {t['classname']}.{t['name']}: {t['message'][:200]}"
            for t in failed_tests[:30]  # Limit to 30 failures
        )

        execution_report_text = (
            f"Total: {surefire_result.get('total', 0)}, "
            f"Passed: {surefire_result.get('passed', 0)}, "
            f"Failed: {surefire_result.get('failed', 0)}, "
            f"Errors: {surefire_result.get('errors', 0)}"
        )

        user_prompt = EXECUTOR_ANALYSIS_PROMPT.format(
            execution_report=execution_report_text,
            failed_tests=failed_details,
            pass_rate=pass_rate,
            target_pass_rate=pass_threshold,
        )

        return self.call_llm_json(
            EXECUTOR_ANALYSIS_SYSTEM,
            user_prompt,
            default={
                "pass_rate": pass_rate,
                "meets_threshold": pass_rate >= pass_threshold,
                "total_tests": surefire_result.get("total", 0),
                "passed": surefire_result.get("passed", 0),
                "failed": surefire_result.get("failed", 0),
                "failed_test_details": [],
                "summary": "Analysis skipped (LLM parse error)",
            },
        )
