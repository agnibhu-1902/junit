# JUnit Generator Pipeline

An automated JUnit 5 test generation pipeline for Spring Boot projects, built with **LangGraph** and the **MCP protocol**.

## Architecture

```
Input (Bitbucket URL or local path)
        │
        ▼
┌─────────────────┐
│  Input Handler  │  Clone repo / validate Maven structure
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ JUnit Generator │◄──────────────────────────────────────┐
│    (LLM)        │  Generates test files in src/test/java │
└────────┬────────┘                                        │
         │                                                 │
         ▼                                                 │
┌─────────────────┐                                        │
│ JUnit Validator │  Validates & auto-fixes test files     │
│    (LLM)        │                                        │
└────────┬────────┘                                        │
         │                                                 │
         ▼                                                 │
┌──────────────────────┐                                   │
│  Compilation Agent   │  mvn test-compile + LLM auto-fix  │
│    (LLM + Maven)     │                                   │
└────────┬─────────────┘                                   │
         │                                                 │
    ┌────┴────┐                                            │
    │ unfixed │──► Human-in-the-Loop                       │
    └────┬────┘                                            │
         │ fixed                                           │
         ▼                                                 │
┌──────────────────┐                                       │
│   JaCoCo Agent   │  mvn jacoco:report + analysis         │
│  (LLM + Maven)   │                                       │
└────────┬─────────┘                                       │
         │                                                 │
    ┌────┴──────────────────────────────────┐              │
    │ coverage < 80%                        │              │
    │ iterations < 5 ───────────────────────┼──────────────┘
    │ iterations ≥ 5 ──► Human-in-the-Loop  │
    └────┬──────────────────────────────────┘
         │ coverage ≥ 80%
         ▼
┌──────────────────────┐
│  Test Executor Agent │  mvn test + Surefire report
│   (LLM + Maven)      │
└────────┬─────────────┘
         │
    ┌────┴──────────────────────────────────┐
    │ pass rate < 80%                       │
    │ iterations < 5 ──► JUnit Generator    │
    │ iterations ≥ 5 ──► Human-in-the-Loop  │
    └────┬──────────────────────────────────┘
         │ pass rate ≥ 80%
         ▼
    Final Report → UI
```

## Setup

### 1. Install dependencies

```bash
cd junit-generator-pipeline
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys and settings
```

### 3. Ensure Maven and Java are installed

```bash
java -version   # Java 11+
mvn -version    # Maven 3.6+
```

## Usage

### CLI — Local directory

```bash
python main.py --project-path /path/to/your/springboot-project
```

### CLI — Bitbucket repository

```bash
python main.py --bitbucket-url https://bitbucket.org/workspace/repo.git
```

### CLI — Custom thresholds

```bash
python main.py \
  --project-path /path/to/project \
  --coverage 85 \
  --pass-rate 90 \
  --max-coverage-iter 3
```

### CLI — JSON output (for UI integration)

```bash
python main.py --project-path /path/to/project --json
```

### MCP Server (for Claude Desktop / other MCP clients)

```bash
python main.py serve
```

Then add to your MCP config (`~/.kiro/settings/mcp.json` or Claude Desktop config):

```json
{
  "mcpServers": {
    "junit-generator-pipeline": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/junit-generator-pipeline",
      "env": {
        "OPENAI_API_KEY": "your-key"
      }
    }
  }
}
```

Available MCP tools:
- `run_pipeline` — Start the pipeline
- `resume_pipeline` — Resume after human review
- `get_pipeline_status` — Check run status

## Human-in-the-Loop

The pipeline pauses automatically when:

| Condition | Trigger |
|-----------|---------|
| Compilation errors remain after 3 auto-fix attempts | `compilation_agent` |
| Coverage < 80% after 5 iterations | `jacoco_agent` |
| Test pass rate < 80% after 5 iterations | `test_executor` |

**CLI**: Interactive prompt appears asking you to apply fixes manually, then continue.

**MCP**: `run_pipeline` returns `status: "paused_for_human_review"` with a `thread_id`. After fixing, call `resume_pipeline(thread_id=..., approved=True)`.

## Project Structure

```
junit-generator-pipeline/
├── agents/
│   ├── base.py               # Shared LLM + tool base class
│   ├── junit_generator.py    # Generates JUnit test files
│   ├── junit_validator.py    # Validates & auto-fixes tests
│   ├── compilation_agent.py  # Compiles & fixes errors
│   ├── jacoco_agent.py       # Coverage analysis
│   └── test_executor.py      # Test execution & analysis
├── config/
│   ├── settings.py           # Environment-based config
│   └── prompts.py            # All LLM prompt templates
├── graph/
│   ├── state.py              # LangGraph TypedDict state
│   ├── nodes.py              # Node functions (agent wrappers)
│   └── workflow.py           # Graph topology & routing
├── mcp_server/
│   ├── server.py             # FastMCP server exposing pipeline
│   └── mcp_config.json       # Example MCP client config
├── tools/
│   └── java_tools.py         # MCP tools: file I/O, Maven, JaCoCo
├── main.py                   # CLI entry point
├── requirements.txt
└── .env.example
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | `openai` or `anthropic` |
| `LLM_MODEL` | `gpt-4o` | Model name |
| `COVERAGE_THRESHOLD` | `80.0` | Minimum JaCoCo coverage % |
| `TEST_PASS_THRESHOLD` | `80.0` | Minimum test pass rate % |
| `MAX_COVERAGE_ITERATIONS` | `5` | Max coverage retry loops |
| `MAX_TEST_PASS_ITERATIONS` | `5` | Max test pass retry loops |
| `MAVEN_CMD` | `mvn` | Maven executable path |
| `BITBUCKET_USERNAME` | — | Bitbucket username |
| `BITBUCKET_APP_PASSWORD` | — | Bitbucket app password |
