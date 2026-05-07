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


MAX_FIX_ATTEMPTS = 3  # Per compilation cycle


class CompilationAgent(BaseAgent):
    """Compiles the project and auto-fixes compilation errors."""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        project_path: str = state["project_path"]
        maven_cmd: str = state.get("maven_cmd", "mvn")

        total_fixed = 0
        compilation_report: list[dict] = []

        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            compile_result = self.invoke_tool(
                "run_maven_compile",
                project_path=project_path,
                maven_cmd=maven_cmd,
            )

            if compile_result["success"]:
                return {
                    **state,
                    "compilation_success": True,
                    "compilation_report": compilation_report,
                    "compilation_fixed_count": total_fixed,
                    "compilation_unfixed_count": 0,
                    "compilation_raw_output": "",
                    "stage": "compilation_passed",
                    "next": "jacoco",
                }

            errors = compile_result.get("errors", [])
            raw_output = compile_result.get("raw_output", "")

            # If structured errors couldn't be parsed, synthesise one entry
            # from the raw Maven output so the LLM can still attempt a fix
            if not errors and raw_output:
                errors = self._extract_errors_from_raw(raw_output, project_path)

            if not errors:
                # Truly unparseable — go straight to human
                return {
                    **state,
                    "compilation_success": False,
                    "compilation_report": [{"error": raw_output or "Unknown error", "fixed": False}],
                    "compilation_fixed_count": total_fixed,
                    "compilation_unfixed_count": 1,
                    "compilation_raw_output": raw_output,
                    "stage": "compilation_failed",
                    "next": "human_loop",
                    "human_loop_reason": (
                        "Compilation failed and errors could not be parsed.\n"
                        f"Maven output:\n{raw_output[-2000:]}"
                    ),
                }

            # Group errors by file and attempt LLM fixes
            errors_by_file = self._group_errors_by_file(errors)
            fixed_this_round = 0

            for file_path, file_errors in errors_by_file.items():
                fix_result = self._attempt_fix(
                    file_path=file_path,
                    errors=file_errors,
                    raw_output=raw_output,
                    project_path=project_path,
                )
                compilation_report.append(fix_result)
                if fix_result["fixed"]:
                    fixed_this_round += 1
                    total_fixed += 1

            if fixed_this_round == 0:
                break  # No progress — stop retrying

        # Final compile check after all fix attempts
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
                "compilation_raw_output": "",
                "stage": "compilation_passed",
                "next": "jacoco",
            }

        remaining_errors = final_compile.get("errors", [])
        raw_output = final_compile.get("raw_output", "")
        total_unfixed = len(remaining_errors) or 1

        return {
            **state,
            "compilation_success": False,
            "compilation_report": compilation_report,
            "compilation_fixed_count": total_fixed,
            "compilation_unfixed_count": total_unfixed,
            "compilation_remaining_errors": remaining_errors,
            "compilation_raw_output": raw_output,
            "stage": "compilation_failed",
            "next": "human_loop",
            "human_loop_reason": (
                f"Compilation failed after {MAX_FIX_ATTEMPTS} fix attempts. "
                f"{total_fixed} issue(s) fixed, {total_unfixed} remain.\n\n"
                f"Maven errors:\n{raw_output[-2000:]}"
            ),
        }

    # -------------------------------------------------------------------------

    def _extract_errors_from_raw(
        self, raw_output: str, project_path: str
    ) -> list[dict[str, str]]:
        """
        Fallback: scan raw Maven output for .java file references and build
        synthetic error entries so the LLM can attempt a fix.
        """
        errors = []
        lines = raw_output.splitlines()
        seen_files: set[str] = set()

        for i, line in enumerate(lines):
            # Match any line mentioning a .java file path
            match = re.search(r"(/[^\s:]+\.java)", line)
            if match:
                fp = match.group(1).strip()
                if fp not in seen_files and Path(fp).exists():
                    seen_files.add(fp)
                    errors.append({
                        "line": line.strip(),
                        "context": "\n".join(lines[max(0, i - 1): i + 4]),
                        "file": fp,
                    })

        # If still nothing, create one entry per test file so LLM can review them
        if not errors:
            test_dir = Path(project_path) / "src" / "test" / "java"
            for java_file in test_dir.rglob("*.java"):
                errors.append({
                    "line": f"Compilation error in {java_file}",
                    "context": raw_output[-1000:],
                    "file": str(java_file),
                })

        return errors

    def _attempt_fix(
        self,
        file_path: str,
        errors: list[dict],
        raw_output: str,
        project_path: str,
    ) -> dict[str, Any]:
        """Use LLM to fix compilation errors in a single file."""
        actual_path = errors[0].get("file", file_path) if errors else file_path

        test_data = self.invoke_tool("read_test_file", test_file_path=actual_path)
        if not test_data["success"]:
            return {"file": actual_path, "fixed": False, "reason": "Could not read file"}

        test_code = test_data["content"]
        source_code = self._find_source_code(actual_path, project_path)

        # Use structured error context if available, else raw Maven output
        error_text = "\n".join(
            e.get("context", e.get("line", "")) for e in errors
        ) or raw_output[-2000:]

        user_prompt = COMPILATION_FIX_PROMPT.format(
            test_file_path=actual_path,
            compilation_error=error_text,
            test_code=test_code,
            source_code=source_code or "Source file not found",
        )

        fixed_code = self.call_llm(COMPILATION_FIX_SYSTEM, user_prompt)
        fixed_code = self._strip_code_fences(fixed_code)

        if not fixed_code.strip():
            return {"file": actual_path, "fixed": False, "reason": "LLM returned empty response"}

        try:
            Path(actual_path).write_text(fixed_code, encoding="utf-8")
            return {
                "file": actual_path,
                "fixed": True,
                "errors_addressed": len(errors),
                "reason": "LLM auto-fix applied",
            }
        except Exception as e:
            return {"file": actual_path, "fixed": False, "reason": str(e)}

    def _group_errors_by_file(self, errors: list[dict]) -> dict[str, list[dict]]:
        """Group compilation errors by their source file path."""
        grouped: dict[str, list[dict]] = {}
        for error in errors:
            # Use explicit 'file' key first, then parse from line
            fp = error.get("file")
            if not fp:
                line = error.get("line", "")
                match = re.search(r"(/[^\s\[]+\.java)", line)
                fp = match.group(1).strip() if match else "unknown"
            grouped.setdefault(fp, []).append(error)
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
        code = re.sub(r"^```[a-zA-Z]*\n?", "", code)
        code = re.sub(r"\n?```\s*$", "", code)
        return code.strip()
