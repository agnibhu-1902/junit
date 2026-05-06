"""
MCP-compatible tools for Java project operations:
file I/O, Maven commands, JaCoCo parsing, test execution parsing.
"""
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("java-tools")


# ---------------------------------------------------------------------------
# File System Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def read_java_file(file_path: str) -> dict[str, Any]:
    """Read a Java source file and return its content with metadata."""
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.suffix == ".java":
        return {"success": False, "error": "Not a Java file"}

    content = path.read_text(encoding="utf-8")

    # Extract package and class name
    package_match = re.search(r"^package\s+([\w.]+)\s*;", content, re.MULTILINE)
    class_match = re.search(
        r"(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum|record)\s+(\w+)", content
    )

    return {
        "success": True,
        "file_path": str(path.absolute()),
        "content": content,
        "package": package_match.group(1) if package_match else "",
        "class_name": class_match.group(1) if class_match else path.stem,
        "lines": len(content.splitlines()),
    }


@mcp.tool()
def write_java_test_file(project_path: str, package_name: str, class_name: str, content: str) -> dict[str, Any]:
    """Write a JUnit test file to the correct location under src/test/java."""
    package_path = package_name.replace(".", os.sep)
    test_dir = Path(project_path) / "src" / "test" / "java" / package_path
    test_dir.mkdir(parents=True, exist_ok=True)

    test_file = test_dir / f"{class_name}Test.java"
    test_file.write_text(content, encoding="utf-8")

    return {
        "success": True,
        "test_file_path": str(test_file.absolute()),
        "message": f"Test file written: {test_file}",
    }


@mcp.tool()
def list_java_source_files(project_path: str) -> dict[str, Any]:
    """List all Java source files in src/main/java, excluding test files."""
    src_dir = Path(project_path) / "src" / "main" / "java"
    if not src_dir.exists():
        return {"success": False, "error": f"src/main/java not found in {project_path}"}

    java_files = []
    for java_file in src_dir.rglob("*.java"):
        # Skip interfaces-only, abstract classes are included
        java_files.append(str(java_file.absolute()))

    return {"success": True, "files": java_files, "count": len(java_files)}


@mcp.tool()
def list_existing_test_files(project_path: str) -> dict[str, Any]:
    """List all existing JUnit test files in src/test/java."""
    test_dir = Path(project_path) / "src" / "test" / "java"
    if not test_dir.exists():
        return {"success": True, "files": [], "count": 0}

    test_files = [str(f.absolute()) for f in test_dir.rglob("*.java")]
    return {"success": True, "files": test_files, "count": len(test_files)}


@mcp.tool()
def read_test_file(test_file_path: str) -> dict[str, Any]:
    """Read an existing test file content."""
    path = Path(test_file_path)
    if not path.exists():
        return {"success": False, "content": "", "error": "File not found"}
    return {"success": True, "content": path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# Maven Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def run_maven_compile(project_path: str, maven_cmd: str = "mvn") -> dict[str, Any]:
    """Run 'mvn test-compile' and return output with success/failure status."""
    result = subprocess.run(
        [maven_cmd, "test-compile", "-q"],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "errors": _parse_compilation_errors(result.stdout + result.stderr),
    }


@mcp.tool()
def run_maven_tests(project_path: str, maven_cmd: str = "mvn") -> dict[str, Any]:
    """Run 'mvn test' and return test results."""
    result = subprocess.run(
        [maven_cmd, "test", "-q"],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = result.stdout + result.stderr
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "test_summary": _parse_maven_test_summary(output),
    }


@mcp.tool()
def run_jacoco_report(project_path: str, maven_cmd: str = "mvn") -> dict[str, Any]:
    """Run 'mvn jacoco:report' and parse the coverage XML report."""
    result = subprocess.run(
        [maven_cmd, "jacoco:report"],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr,
            "coverage_percentage": 0.0,
        }

    return _parse_jacoco_xml(project_path)


@mcp.tool()
def run_maven_verify(project_path: str, maven_cmd: str = "mvn") -> dict[str, Any]:
    """Run 'mvn verify' (compile + test + jacoco) in one shot."""
    result = subprocess.run(
        [maven_cmd, "verify"],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = result.stdout + result.stderr
    coverage = _parse_jacoco_xml(project_path)
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "test_summary": _parse_maven_test_summary(output),
        "coverage": coverage,
    }


# ---------------------------------------------------------------------------
# Surefire Report Parser
# ---------------------------------------------------------------------------

@mcp.tool()
def parse_surefire_reports(project_path: str) -> dict[str, Any]:
    """Parse Maven Surefire XML reports to get detailed test pass/fail info."""
    surefire_dir = Path(project_path) / "target" / "surefire-reports"
    if not surefire_dir.exists():
        return {"success": False, "error": "No surefire reports found", "tests": []}

    all_tests = []
    total = passed = failed = errors = skipped = 0

    for xml_file in surefire_dir.glob("TEST-*.xml"):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            suite_name = root.attrib.get("name", "")
            suite_tests = int(root.attrib.get("tests", 0))
            suite_failures = int(root.attrib.get("failures", 0))
            suite_errors = int(root.attrib.get("errors", 0))
            suite_skipped = int(root.attrib.get("skipped", 0))

            total += suite_tests
            failed += suite_failures
            errors += suite_errors
            skipped += suite_skipped
            passed += suite_tests - suite_failures - suite_errors - suite_skipped

            for testcase in root.findall("testcase"):
                test_info = {
                    "suite": suite_name,
                    "name": testcase.attrib.get("name", ""),
                    "classname": testcase.attrib.get("classname", ""),
                    "time": float(testcase.attrib.get("time", 0)),
                    "status": "passed",
                    "message": "",
                }
                failure = testcase.find("failure")
                error = testcase.find("error")
                skip = testcase.find("skipped")
                if failure is not None:
                    test_info["status"] = "failed"
                    test_info["message"] = failure.attrib.get("message", failure.text or "")
                elif error is not None:
                    test_info["status"] = "error"
                    test_info["message"] = error.attrib.get("message", error.text or "")
                elif skip is not None:
                    test_info["status"] = "skipped"
                all_tests.append(test_info)
        except ET.ParseError:
            continue

    pass_rate = round((passed / total * 100), 2) if total > 0 else 0.0
    return {
        "success": True,
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "pass_rate": pass_rate,
        "tests": all_tests,
        "failed_tests": [t for t in all_tests if t["status"] in ("failed", "error")],
    }


# ---------------------------------------------------------------------------
# Bitbucket / Git Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def clone_bitbucket_repo(
    repo_url: str,
    clone_dir: str,
    username: str = "",
    app_password: str = "",
) -> dict[str, Any]:
    """Clone a Bitbucket repository to a local directory."""
    import git

    clone_path = Path(clone_dir)
    clone_path.mkdir(parents=True, exist_ok=True)

    # Inject credentials into URL if provided
    if username and app_password:
        # https://user:pass@bitbucket.org/workspace/repo.git
        repo_url = repo_url.replace("https://", f"https://{username}:{app_password}@")

    try:
        repo = git.Repo.clone_from(repo_url, clone_path)
        return {
            "success": True,
            "clone_path": str(clone_path.absolute()),
            "branch": repo.active_branch.name,
            "message": f"Cloned to {clone_path}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def validate_project_structure(project_path: str) -> dict[str, Any]:
    """Validate that the given path is a Maven Spring Boot project."""
    path = Path(project_path)
    checks = {
        "path_exists": path.exists(),
        "pom_xml": (path / "pom.xml").exists(),
        "src_main": (path / "src" / "main" / "java").exists(),
        "src_test": (path / "src" / "test" / "java").exists(),
    }
    is_valid = all(checks.values())

    # Create src/test/java if missing
    if checks["path_exists"] and checks["pom_xml"] and not checks["src_test"]:
        (path / "src" / "test" / "java").mkdir(parents=True, exist_ok=True)
        checks["src_test"] = True
        is_valid = True

    return {"success": is_valid, "checks": checks, "project_path": str(path.absolute())}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_compilation_errors(output: str) -> list[dict[str, str]]:
    """Extract compilation errors from Maven output."""
    errors = []
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if "[ERROR]" in line and ".java:" in line:
            errors.append({
                "line": line.strip(),
                "context": "\n".join(lines[max(0, i - 1): i + 3]),
            })
    return errors


def _parse_maven_test_summary(output: str) -> dict[str, Any]:
    """Parse Maven Surefire summary line from stdout."""
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    match = re.search(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)",
        output,
    )
    if match:
        summary = {
            "tests": int(match.group(1)),
            "failures": int(match.group(2)),
            "errors": int(match.group(3)),
            "skipped": int(match.group(4)),
        }
    return summary


def _parse_jacoco_xml(project_path: str) -> dict[str, Any]:
    """Parse JaCoCo XML report and compute overall instruction coverage."""
    jacoco_xml = Path(project_path) / "target" / "site" / "jacoco" / "jacoco.xml"
    if not jacoco_xml.exists():
        return {"success": False, "coverage_percentage": 0.0, "error": "jacoco.xml not found"}

    try:
        tree = ET.parse(jacoco_xml)
        root = tree.getroot()

        total_missed = total_covered = 0
        class_coverage = []

        for package in root.findall("package"):
            for cls in package.findall("class"):
                cls_name = cls.attrib.get("name", "").replace("/", ".")
                for counter in cls.findall("counter"):
                    if counter.attrib.get("type") == "INSTRUCTION":
                        missed = int(counter.attrib.get("missed", 0))
                        covered = int(counter.attrib.get("covered", 0))
                        total = missed + covered
                        pct = round(covered / total * 100, 2) if total > 0 else 0.0
                        class_coverage.append({
                            "class": cls_name,
                            "covered": covered,
                            "missed": missed,
                            "coverage_pct": pct,
                        })
                        total_missed += missed
                        total_covered += total_covered + covered

        grand_total = total_missed + total_covered
        overall_pct = round(total_covered / grand_total * 100, 2) if grand_total > 0 else 0.0

        # Sort by coverage ascending (lowest first = most needing attention)
        class_coverage.sort(key=lambda x: x["coverage_pct"])

        return {
            "success": True,
            "coverage_percentage": overall_pct,
            "total_instructions": grand_total,
            "covered_instructions": total_covered,
            "missed_instructions": total_missed,
            "class_coverage": class_coverage,
            "low_coverage_classes": [c for c in class_coverage if c["coverage_pct"] < 80],
        }
    except ET.ParseError as e:
        return {"success": False, "coverage_percentage": 0.0, "error": str(e)}
