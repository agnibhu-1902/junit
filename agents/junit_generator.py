"""
JUnit Generator Agent
Generates JUnit 5 test files for all Java source files in the project.
Supports fine-tuned re-generation based on coverage gaps or test failures.
"""
from __future__ import annotations

import re
from typing import Any

from agents.base import BaseAgent
from config.prompts import (
    JUNIT_GENERATOR_SYSTEM,
    JUNIT_GENERATOR_PROMPT,
    JUNIT_GENERATOR_FINETUNE_COVERAGE,
    JUNIT_GENERATOR_FINETUNE_FAILURES,
)


class JUnitGeneratorAgent(BaseAgent):
    """Generates JUnit test files for a Spring Boot project."""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Entry point called by LangGraph.
        Reads source files, generates tests, writes them to src/test/java.
        """
        project_path: str = state["project_path"]
        fine_tune_type: str = state.get("fine_tune_type", "")  # "coverage" | "failures" | ""
        coverage_data: dict = state.get("coverage_data", {})
        failure_data: dict = state.get("failure_data", {})

        # List all source files
        source_result = self.invoke_tool("list_java_source_files", project_path=project_path)
        if not source_result["success"]:
            return {**state, "error": source_result.get("error"), "stage": "generator_failed"}

        source_files: list[str] = source_result["files"]
        generated_files: list[str] = []
        generation_errors: list[str] = []

        for file_path in source_files:
            try:
                result = self._generate_test_for_file(
                    file_path=file_path,
                    project_path=project_path,
                    fine_tune_type=fine_tune_type,
                    coverage_data=coverage_data,
                    failure_data=failure_data,
                )
                if result:
                    generated_files.append(result)
            except Exception as e:
                generation_errors.append(f"{file_path}: {str(e)}")

        return {
            **state,
            "generated_test_files": generated_files,
            "generation_errors": generation_errors,
            "stage": "junit_generated",
            "fine_tune_type": "",  # Reset after generation
        }

    def _generate_test_for_file(
        self,
        file_path: str,
        project_path: str,
        fine_tune_type: str,
        coverage_data: dict,
        failure_data: dict,
    ) -> str | None:
        """Generate a test file for a single Java source file."""
        # Read source file
        file_data = self.invoke_tool("read_java_file", file_path=file_path)
        if not file_data["success"]:
            return None

        source_code = file_data["content"]
        package_name = file_data["package"]
        class_name = file_data["class_name"]

        # Skip if it's already a test file
        if class_name.endswith("Test") or class_name.endswith("Tests"):
            return None

        # Skip interfaces (no implementation to test)
        if re.search(r"^\s*(?:public\s+)?interface\s+", source_code, re.MULTILINE):
            return None

        # Determine test package (mirror main package)
        test_package = package_name

        # Read existing test file if present
        existing_tests_result = self.invoke_tool(
            "list_existing_test_files", project_path=project_path
        )
        existing_test_content = ""
        if existing_tests_result["success"]:
            for tf in existing_tests_result["files"]:
                if f"{class_name}Test.java" in tf:
                    content_result = self.invoke_tool("read_test_file", test_file_path=tf)
                    if content_result["success"]:
                        existing_test_content = content_result["content"]
                    break

        # Build fine-tune instructions
        fine_tune_instructions = self._build_fine_tune_instructions(
            fine_tune_type=fine_tune_type,
            class_name=class_name,
            coverage_data=coverage_data,
            failure_data=failure_data,
        )

        # Build prompt
        user_prompt = JUNIT_GENERATOR_PROMPT.format(
            file_path=file_path,
            package_name=package_name,
            class_name=class_name,
            source_code=source_code,
            existing_tests=existing_test_content or "None",
            fine_tune_instructions=fine_tune_instructions,
            test_package=test_package,
        )

        # Call LLM
        test_code = self.call_llm(JUNIT_GENERATOR_SYSTEM, user_prompt)

        # Strip markdown fences if LLM wrapped in code block
        test_code = self._strip_code_fences(test_code)

        # Write test file
        write_result = self.invoke_tool(
            "write_java_test_file",
            project_path=project_path,
            package_name=test_package,
            class_name=class_name,
            content=test_code,
        )

        if write_result["success"]:
            return write_result["test_file_path"]
        return None

    def _build_fine_tune_instructions(
        self,
        fine_tune_type: str,
        class_name: str,
        coverage_data: dict,
        failure_data: dict,
    ) -> str:
        """Build fine-tuning instructions based on previous run results."""
        if fine_tune_type == "coverage":
            current_coverage = coverage_data.get("coverage_percentage", 0)
            target_coverage = coverage_data.get("target", 80)
            # Find uncovered lines for this specific class
            uncovered = []
            for cls in coverage_data.get("low_coverage_classes", []):
                if class_name in cls.get("class", ""):
                    uncovered.append(
                        f"  - {cls['class']}: {cls['coverage_pct']}% covered, "
                        f"{cls['missed']} instructions missed"
                    )
            return JUNIT_GENERATOR_FINETUNE_COVERAGE.format(
                current_coverage=current_coverage,
                target_coverage=target_coverage,
                uncovered_lines="\n".join(uncovered) or "See coverage report for details",
            )

        elif fine_tune_type == "failures":
            failures = failure_data.get("failed_test_details", [])
            # Filter failures relevant to this class
            relevant = [
                f"  - {f['test']}: {f['error']}"
                for f in failures
                if class_name in f.get("test", "")
            ]
            return JUNIT_GENERATOR_FINETUNE_FAILURES.format(
                failure_count=len(failures),
                pass_rate=failure_data.get("pass_rate", 0),
                failure_details="\n".join(relevant) or "See execution report for details",
            )

        return ""

    @staticmethod
    def _strip_code_fences(code: str) -> str:
        """Remove markdown code fences from LLM output."""
        code = code.strip()
        code = re.sub(r"^```[a-z]*\n?", "", code)
        code = re.sub(r"\n?```$", "", code)
        return code.strip()
