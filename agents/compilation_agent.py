"""
Compilation Agent
Compiles the project, auto-fixes compilation errors using the LLM,
and reports on fixed vs unfixed issues.
Triggers human-in-the-loop if any issues remain unfixed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from config.prompts import COMPILATION_FIX_SYSTEM, COMPILATION_FIX_PROMPT


MAX_FIX_ATTEMPTS = 3  # Per file, per compilation cycle


class CompilationAgent(BaseAgent):
    """Compiles the project and auto-fixes compilation errors."""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        1. Run mvn test-compile
        2. If errors exist, attempt LLM-based auto-fix (up to MAX_FIX_ATTEMPTS)
        3. Report fixed vs unfixed
        4. Route to human-in-the-loop if unfixed issues remain
        """
        project_path: str = state["project_path"]
        maven_cmd: str = state.get("maven_cmd", "mvn")

        total_fixed = 0
        total_unfixed = 0
        compilation_report: list[dict] = []

        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            compile_result = self.invoke_tool(
                "run_maven_compile",
                project_path=project_path,
                maven_cmd=maven_cmd,
            )

            if compile_result["success"]:
                # All good — no compilation errors
                return {
                    **state,
                    "compilation_success": True,
                    "compilation_report": compilation_report,
                    "compilation_fixed_count": total_fixed,
                    "compilation_unfixed_count": 0,
                    "stage": "compilation_passed",
                    "next": "jacoco",
                }

            errors = compile_result.get("errors", [])
            if not errors:
                # Maven failed but no parseable errors (e.g. missing dependency)
                return {
                    **state,
                    "compilation_success": False,
                    "compilation_report": [{"error": compile_result.get("stderr", "Unknown error")}],
                    "compilation_fixed_count": total_fixed,
                    "compilation_unfixed_count": 1,
                    "stage": "compilation_failed",
                    "next": "human_loop",
                    "human_loop_reason": "Compilation failed with unparseable errors. Manual intervention required.",
                }

            # Group errors by file
            errors_by_file = self._group_errors_by_file(errors)
            fixed_this_round = 0

            for file_path, file_errors in errors_by_file.items():
                fix_result = self._attempt_fix(
                    file_path=file_path,
                    errors=file_errors,
                    project_path=project_path,
                )
                compilation_report.append(fix_result)
                if fix_result["fixed"]:
                    fixed_this_round += 1
                    total_fixed += 1

            if fixed_this_round == 0:
                # No progress — stop trying
                break

        # Final compile check
        final_compile = self.invoke_tool(
            "run_maven_compile",
            project_path=project_path,
            maven_cmd=maven_cmd,
        )

        if final_compile["success"]:
            return {
                **state,
                "compilation_success": True,
                "compilation_report": compilation_report,
                "compilation_fixed_count": total_fixed,
                "compilation_unfixed_count": 0,
                "stage": "compilation_passed",
                "next": "jacoco",
            }

        # Still failing — human in the loop
        remaining_errors = final_compile.get("errors", [])
        total_unfixed = len(remaining_errors)

        return {
            **state,
            "compilation_success": False,
            "compilation_report": compilation_report,
            "compilation_fixed_count": total_fixed,
            "compilation_unfixed_count": total_unfixed,
            "compilation_remaining_errors": remaining_errors,
            "stage": "compilation_failed",
            "next": "human_loop",
            "human_loop_reason": (
                f"Compilation failed after {MAX_FIX_ATTEMPTS} fix attempts. "
                f"{total_fixed} issues fixed, {total_unfixed} remain. "
                "Manual intervention required."
            ),
        }

    def _attempt_fix(
        self, file_path: str, errors: list[dict], project_path: str
    ) -> dict[str, Any]:
        """Use LLM to fix compilation errors in a single file."""
        # Read the test file
        test_data = self.invoke_tool("read_test_file", test_file_path=file_path)
        if not test_data["success"]:
            return {"file": file_path, "fixed": False, "reason": "Could not read file"}

        test_code = test_data["content"]

        # Find corresponding source file
        source_code = self._find_source_code(file_path, project_path)

        error_text = "\n".join(e.get("context", e.get("line", "")) for e in errors)

        user_prompt = COMPILATION_FIX_PROMPT.format(
            test_file_path=file_path,
            compilation_error=error_text,
            test_code=test_code,
            source_code=source_code or "Source file not found",
        )

        fixed_code = self.call_llm(COMPILATION_FIX_SYSTEM, user_prompt)
        fixed_code = self._strip_code_fences(fixed_code)

        # Write fixed code back
        try:
            Path(file_path).write_text(fixed_code, encoding="utf-8")
            return {
                "file": file_path,
                "fixed": True,
                "errors_addressed": len(errors),
                "reason": "LLM auto-fix applied",
            }
        except Exception as e:
            return {"file": file_path, "fixed": False, "reason": str(e)}

    def _group_errors_by_file(self, errors: list[dict]) -> dict[str, list[dict]]:
        """Group compilation errors by their source file path."""
        grouped: dict[str, list[dict]] = {}
        for error in errors:
            line = error.get("line", "")
            # Extract file path from Maven error line: [ERROR] /path/to/File.java:[10,5] ...
            match = re.search(r"\[ERROR\]\s+(/[^\[]+\.java)", line)
            if match:
                fp = match.group(1).strip()
                grouped.setdefault(fp, []).append(error)
            else:
                grouped.setdefault("unknown", []).append(error)
        return grouped

    def _find_source_code(self, test_file_path: str, project_path: str) -> str | None:
        """Find the source file corresponding to a test file."""
        test_path = Path(test_file_path)
        class_name = test_path.stem.removesuffix("Test").removesuffix("Tests")
        src_dir = Path(project_path) / "src" / "main" / "java"
        for java_file in src_dir.rglob(f"{class_name}.java"):
            data = self.invoke_tool("read_java_file", file_path=str(java_file))
            if data["success"]:
                return data["content"]
        return None

    @staticmethod
    def _strip_code_fences(code: str) -> str:
        code = code.strip()
        code = re.sub(r"^```[a-z]*\n?", "", code)
        code = re.sub(r"\n?```$", "", code)
        return code.strip()
