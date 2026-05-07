"""
JaCoCo Agent
Runs Maven JaCoCo plugin, parses the coverage report,
and decides whether to loop back to the generator or proceed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from config.prompts import JACOCO_ANALYSIS_SYSTEM, JACOCO_ANALYSIS_PROMPT
from config.settings import settings


class JacocoAgent(BaseAgent):
    """Generates and evaluates JaCoCo code coverage reports."""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        project_path: str = state["project_path"]
        maven_cmd: str = state.get("maven_cmd", "mvn")
        coverage_iterations: int = state.get("coverage_iterations", 0)
        coverage_threshold: float = state.get("coverage_threshold", settings.COVERAGE_THRESHOLD)
        max_iterations: int = state.get("max_coverage_iterations", settings.MAX_COVERAGE_ITERATIONS)

        # Ensure JaCoCo is in pom.xml before running — fixes infinite loop
        # caused by missing plugin returning 0% every iteration
        self._ensure_jacoco_in_pom(project_path)

        # Run tests + jacoco report via mvn verify (most reliable)
        coverage_result = self._run_verify(project_path, maven_cmd)

        # Detect plugin-not-found error — treat as infrastructure failure
        error_msg = coverage_result.get("error", "")
        if "NoPluginFoundForPrefixException" in error_msg or "No plugin found for prefix 'jacoco'" in error_msg:
            # pom.xml was just fixed — retry once
            self._ensure_jacoco_in_pom(project_path)
            coverage_result = self._run_verify(project_path, maven_cmd)

        coverage_pct = coverage_result.get("coverage_percentage", 0.0)
        coverage_iterations += 1

        analysis = self._analyze_coverage(project_path, coverage_result, coverage_threshold)

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

        return {
            **state,
            "coverage_data": updated_coverage_data,
            "coverage_iterations": coverage_iterations,
            "coverage_met": False,
            "fine_tune_type": "coverage",
            "stage": "coverage_below_threshold",
            "next": "junit_generator",
        }

    # -------------------------------------------------------------------------

    def _ensure_jacoco_in_pom(self, project_path: str) -> None:
        """Add JaCoCo plugin to pom.xml if it's not already there."""
        pom_path = Path(project_path) / "pom.xml"
        if not pom_path.exists():
            return

        content = pom_path.read_text(encoding="utf-8")
        if "jacoco-maven-plugin" in content:
            return  # Already present

        jacoco_plugin = """
                        <plugin>
                            <groupId>org.jacoco</groupId>
                            <artifactId>jacoco-maven-plugin</artifactId>
                            <version>0.8.11</version>
                            <executions>
                                <execution>
                                    <goals><goal>prepare-agent</goal></goals>
                                </execution>
                                <execution>
                                    <id>report</id>
                                    <phase>test</phase>
                                    <goals><goal>report</goal></goals>
                                </execution>
                            </executions>
                        </plugin>"""

        if "</plugins>" in content:
            content = content.replace(
                "</plugins>", jacoco_plugin + "\n                </plugins>", 1
            )
        elif "</build>" in content:
            content = content.replace(
                "</build>",
                f"\n        <plugins>{jacoco_plugin}\n        </plugins>\n        </build>",
                1,
            )
        else:
            return  # Can't safely inject

        pom_path.write_text(content, encoding="utf-8")

    def _run_verify(self, project_path: str, maven_cmd: str) -> dict[str, Any]:
        """Run mvn verify to compile, test, and generate JaCoCo report in one shot."""
        result = self.invoke_tool(
            "run_maven_verify",
            project_path=project_path,
            maven_cmd=maven_cmd,
        )
        coverage = result.get("coverage", {})

        # If jacoco.xml wasn't generated, try standalone jacoco:report
        if not coverage.get("success"):
            coverage = self.invoke_tool(
                "run_jacoco_report",
                project_path=project_path,
                maven_cmd=maven_cmd,
            )

        return coverage if coverage else {"success": False, "coverage_percentage": 0.0}

    def _analyze_coverage(
        self,
        project_path: str,
        coverage_result: dict,
        coverage_threshold: float,
    ) -> dict[str, Any]:
        coverage_pct = coverage_result.get("coverage_percentage", 0.0)
        low_coverage = coverage_result.get("low_coverage_classes", [])

        uncovered_details = "\n".join(
            f"  - {c['class']}: {c['coverage_pct']}% ({c['missed']} instructions missed)"
            for c in low_coverage[:20]
        )

        user_prompt = JACOCO_ANALYSIS_PROMPT.format(
            project_path=project_path,
            current_coverage=coverage_pct,
            target_coverage=coverage_threshold,
            coverage_gap=max(0, coverage_threshold - coverage_pct),
            coverage_report=f"Overall: {coverage_pct}%",
            uncovered_details=uncovered_details or "No detailed data available",
        )

        return self.call_llm_json(
            JACOCO_ANALYSIS_SYSTEM,
            user_prompt,
            default={
                "coverage_percentage": coverage_pct,
                "meets_threshold": coverage_pct >= coverage_threshold,
                "uncovered_classes": low_coverage[:10],
                "recommendations": [],
            },
        )
