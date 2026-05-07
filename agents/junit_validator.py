"""
JUnit Validator Agent
Validates generated test files for correctness before compilation.
Auto-fixes issues found during validation.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from config.prompts import JUNIT_VALIDATOR_SYSTEM, JUNIT_VALIDATOR_PROMPT


class JUnitValidatorAgent(BaseAgent):
    """Validates and auto-corrects generated JUnit test files."""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Validate all generated test files.
        Auto-fix issues found by the LLM validator.
        """
        project_path: str = state["project_path"]
        generated_files: list[str] = state.get("generated_test_files", [])

        validation_results: list[dict] = []
        fixed_count = 0
        invalid_count = 0

        # Get all test files (generated + pre-existing)
        all_tests_result = self.invoke_tool("list_existing_test_files", project_path=project_path)
        test_files = all_tests_result.get("files", []) if all_tests_result["success"] else generated_files

        for test_file_path in test_files:
            result = self._validate_test_file(test_file_path, project_path)
            validation_results.append(result)

            if not result["is_valid"]:
                if result.get("fixed_code"):
                    # Write the fixed version back
                    Path(test_file_path).write_text(result["fixed_code"], encoding="utf-8")
                    fixed_count += 1
                    result["auto_fixed"] = True
                elif result.get("regenerate"):
                    # File is completely wrong (e.g. XML in a .java file) — delete and
                    # let the generator re-create it on the next iteration
                    Path(test_file_path).unlink(missing_ok=True)
                    fixed_count += 1
                    result["auto_fixed"] = True
                    result["summary"] += " (file deleted for regeneration)"
                else:
                    invalid_count += 1
                    result["auto_fixed"] = False

        return {
            **state,
            "validation_results": validation_results,
            "validation_fixed_count": fixed_count,
            "validation_invalid_count": invalid_count,
            "stage": "junit_validated",
        }

    def _validate_test_file(self, test_file_path: str, project_path: str) -> dict[str, Any]:
        """Validate a single test file using the LLM."""
        # Read test file
        test_data = self.invoke_tool("read_test_file", test_file_path=test_file_path)
        if not test_data["success"]:
            return {
                "test_file": test_file_path,
                "is_valid": False,
                "issues": ["Could not read test file"],
                "fixed_code": None,
                "summary": "File read error",
            }

        test_code = test_data["content"]

        # Detect completely non-Java content (e.g. XML, JSON, plain text)
        # If the file doesn't look like Java at all, flag for regeneration
        is_java = (
            "package " in test_code
            or "import " in test_code
            or "class " in test_code
            or "@Test" in test_code
        )
        if not is_java:
            return {
                "test_file": test_file_path,
                "is_valid": False,
                "issues": ["File does not contain valid Java code — likely LLM output error"],
                "fixed_code": None,
                "regenerate": True,
                "summary": "File contains non-Java content and will be regenerated",
            }

        # Find corresponding source file
        source_code = self._find_source_code(test_file_path, project_path)

        user_prompt = JUNIT_VALIDATOR_PROMPT.format(
            test_file_path=test_file_path,
            source_file_path="(source file)",
            test_code=test_code,
            source_code=source_code or "Source file not found",
        )

        result = self.call_llm_json(
            JUNIT_VALIDATOR_SYSTEM,
            user_prompt,
            default={"is_valid": True, "issues": [], "fixed_code": None, "summary": "Validation skipped (LLM parse error)"},
        )

        return {
            "test_file": test_file_path,
            "is_valid": result.get("is_valid", True),
            "issues": result.get("issues", []),
            "fixed_code": result.get("fixed_code"),
            "summary": result.get("summary", ""),
        }

    def _find_source_code(self, test_file_path: str, project_path: str) -> str | None:
        """Attempt to locate the source file corresponding to a test file."""
        test_path = Path(test_file_path)
        # e.g. src/test/java/com/example/FooTest.java -> src/main/java/com/example/Foo.java
        class_name = test_path.stem.removesuffix("Test").removesuffix("Tests")
        src_dir = Path(project_path) / "src" / "main" / "java"

        for java_file in src_dir.rglob(f"{class_name}.java"):
            data = self.invoke_tool("read_java_file", file_path=str(java_file))
            if data["success"]:
                return data["content"]
        return None
