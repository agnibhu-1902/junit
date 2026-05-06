"""
Prompt templates for all agents in the JUnit Generator Pipeline.
"""

# ---------------------------------------------------------------------------
# JUnit Generator
# ---------------------------------------------------------------------------

JUNIT_GENERATOR_SYSTEM = """You are an expert Java developer specializing in writing comprehensive JUnit 5 test cases
for Spring Boot applications. You follow best practices including:
- Using @ExtendWith(MockitoExtension.class) for unit tests
- Using @SpringBootTest for integration tests where appropriate
- Mocking dependencies with @Mock / @InjectMocks
- Writing meaningful assertions with AssertJ or JUnit assertions
- Covering happy paths, edge cases, and exception scenarios
- Ensuring tests are independent and repeatable
- Following AAA (Arrange-Act-Assert) pattern
"""

JUNIT_GENERATOR_PROMPT = """Analyze the following Java source file and generate comprehensive JUnit 5 test cases.

Source File: {file_path}
Package: {package_name}
Class Name: {class_name}

Source Code:
```java
{source_code}
```

Existing Test Files (if any):
{existing_tests}

{fine_tune_instructions}

Generate a complete JUnit 5 test file that:
1. Tests all public methods thoroughly
2. Covers edge cases and exception scenarios
3. Uses Mockito for mocking dependencies
4. Follows Spring Boot testing conventions
5. Includes proper imports

Return ONLY the Java test file content, no explanations.
The test class should be placed in package: {test_package}
"""

JUNIT_GENERATOR_FINETUNE_COVERAGE = """
IMPORTANT - Fine-tuning Instructions (Coverage Improvement):
The previous test run achieved only {current_coverage}% code coverage (target: {target_coverage}%).
The following lines/methods are NOT covered:
{uncovered_lines}

Please generate ADDITIONAL test cases specifically targeting these uncovered areas.
Focus on:
- The uncovered methods listed above
- Branch conditions not yet tested
- Exception paths not yet covered
"""

JUNIT_GENERATOR_FINETUNE_FAILURES = """
IMPORTANT - Fine-tuning Instructions (Test Failure Fix):
The previous test run had {failure_count} failing tests (pass rate: {pass_rate}%).
Failing tests and their errors:
{failure_details}

Please fix the failing test cases. Common issues to address:
- Incorrect mock setup
- Wrong expected values
- Missing test data setup
- Incorrect exception handling in tests
"""

# ---------------------------------------------------------------------------
# JUnit Validator
# ---------------------------------------------------------------------------

JUNIT_VALIDATOR_SYSTEM = """You are a Java code reviewer specializing in JUnit test quality assurance.
You validate test files for correctness, completeness, and adherence to best practices."""

JUNIT_VALIDATOR_PROMPT = """Review the following JUnit test file for correctness and quality.

Test File: {test_file_path}
Source File Being Tested: {source_file_path}

Test Code:
```java
{test_code}
```

Source Code:
```java
{source_code}
```

Validate the following:
1. All imports are correct and complete
2. Test class structure is valid (annotations, extends, etc.)
3. Mock setup is correct for all dependencies
4. Test method signatures are valid (@Test annotation, void return, etc.)
5. Assertions are meaningful and correct
6. No obvious compilation errors
7. Test method names follow conventions

Return a JSON response with this structure:
{{
  "is_valid": true/false,
  "issues": ["list of issues found"],
  "fixed_code": "corrected Java code if issues found, null if valid",
  "summary": "brief validation summary"
}}
"""

# ---------------------------------------------------------------------------
# Compilation Agent
# ---------------------------------------------------------------------------

COMPILATION_FIX_SYSTEM = """You are an expert Java developer who specializes in fixing compilation errors
in JUnit test files for Spring Boot applications."""

COMPILATION_FIX_PROMPT = """Fix the following compilation error in the JUnit test file.

Test File: {test_file_path}
Compilation Error:
{compilation_error}

Current Test Code:
```java
{test_code}
```

Source Code Being Tested:
```java
{source_code}
```

Fix the compilation error and return ONLY the corrected Java code.
Common fixes needed:
- Add missing imports
- Fix incorrect method signatures
- Correct mock annotations
- Fix type mismatches
- Add missing dependencies in pom.xml if needed (list them separately)
"""

# ---------------------------------------------------------------------------
# Jacoco Agent
# ---------------------------------------------------------------------------

JACOCO_ANALYSIS_SYSTEM = """You are a Java code coverage expert who analyzes JaCoCo reports
and provides actionable insights for improving test coverage."""

JACOCO_ANALYSIS_PROMPT = """Analyze the JaCoCo coverage report and identify areas needing more tests.

Project Path: {project_path}
Current Coverage: {current_coverage}%
Target Coverage: {target_coverage}%
Coverage Gap: {coverage_gap}%

Coverage Report Summary:
{coverage_report}

Uncovered Classes/Methods:
{uncovered_details}

Provide a structured analysis with:
1. List of classes with lowest coverage
2. Specific methods that need test coverage
3. Recommended test scenarios for each uncovered area

Return as JSON:
{{
  "coverage_percentage": {current_coverage},
  "meets_threshold": true/false,
  "uncovered_classes": [{{"class": "...", "coverage": 0.0, "uncovered_methods": []}}],
  "recommendations": ["..."]
}}
"""

# ---------------------------------------------------------------------------
# JUnit Test Executor
# ---------------------------------------------------------------------------

EXECUTOR_ANALYSIS_SYSTEM = """You are a Java testing expert who analyzes test execution results
and provides fixes for failing tests."""

EXECUTOR_ANALYSIS_PROMPT = """Analyze the test execution results and identify root causes of failures.

Test Execution Report:
{execution_report}

Failed Tests:
{failed_tests}

Pass Rate: {pass_rate}%
Target: {target_pass_rate}%

For each failing test, identify:
1. Root cause of failure
2. Whether it's a test issue or source code issue
3. Recommended fix

Return as JSON:
{{
  "pass_rate": {pass_rate},
  "meets_threshold": true/false,
  "total_tests": 0,
  "passed": 0,
  "failed": 0,
  "failed_test_details": [{{"test": "...", "error": "...", "root_cause": "...", "fix": "..."}}],
  "summary": "..."
}}
"""
