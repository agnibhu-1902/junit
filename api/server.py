"""
FastAPI server for the JUnit Generator Pipeline.

Endpoints:
  POST   /api/pipeline/run              — start a run, returns run_id
  GET    /api/pipeline/{run_id}/stream  — SSE stream of real-time events
  GET    /api/pipeline/{run_id}/status  — current state snapshot
  GET    /api/pipeline/{run_id}/report  — final report (when completed)
  POST   /api/pipeline/{run_id}/resume  — resume after human-in-the-loop pause
  DELETE /api/pipeline/{run_id}         — abort a run
  GET    /api/runs                      — list all runs
  GET    /api/health                    — health check
"""
from __future__ import annotations

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from graph import build_pipeline, PipelineState
from config.settings import settings

app = FastAPI(
    title="JUnit Generator Pipeline API",
    description="REST + SSE API for the automated JUnit test generation pipeline",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# In-memory run store
# ---------------------------------------------------------------------------

class RunStore:
    def __init__(self):
        self._runs: dict[str, dict[str, Any]] = {}

    def create(self, run_id: str, initial_state: dict) -> dict:
        run = {
            "run_id": run_id,
            "status": "pending",   # pending|running|paused|completed|failed|aborted
            "stage": "",
            "started_at": _now(),
            "completed_at": None,
            "pipeline": None,
            "config": None,
            "state": initial_state,
            "events": [],          # full event history for late subscribers
            "final_report": None,
            "human_loop_reason": None,
            "error": None,
            "_subscribers": [],    # per-subscriber SSE queues
            "_loop": None,
        }
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> dict | None:
        return self._runs.get(run_id)

    def delete(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def list_runs(self) -> list[dict]:
        return [
            {k: v for k, v in r.items() if k not in ("pipeline", "_subscribers", "_loop")}
            for r in self._runs.values()
        ]


store = RunStore()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    project_path: str = ""
    bitbucket_repo_url: str = ""
    coverage_threshold: float = 80.0
    pass_threshold: float = 80.0
    max_coverage_iterations: int = 5
    max_test_pass_iterations: int = 5
    maven_cmd: str = "mvn"


class ResumeRequest(BaseModel):
    approved: bool = True
    feedback: str = ""


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def _make_event(event_type: str, data: dict) -> dict:
    return {"type": event_type, "timestamp": _now(), "data": data}


def _push_event(run: dict, event_type: str, data: dict) -> None:
    """Append event to history and push to ALL subscriber queues (thread-safe)."""
    event = _make_event(event_type, data)
    run["events"].append(event)
    if data.get("stage"):
        run["stage"] = data["stage"]

    loop: asyncio.AbstractEventLoop | None = run.get("_loop")
    if loop:
        for q in run.get("_subscribers", []):
            loop.call_soon_threadsafe(q.put_nowait, event)


async def _sse_generator(run_id: str) -> AsyncGenerator[str, None]:
    """
    Yield SSE-formatted events.
    Each call gets its own queue — no duplicates, no shared state.
    History is replayed first, then live events are streamed.
    """
    run = store.get(run_id)
    if not run:
        yield f"data: {json.dumps({'type':'error','data':{'message':'Run not found'}})}\n\n"
        return

    # Create a dedicated queue for this subscriber
    my_queue: asyncio.Queue = asyncio.Queue()
    run.setdefault("_subscribers", []).append(my_queue)

    try:
        # Replay history snapshot so late subscribers catch up
        for event in list(run["events"]):
            yield f"data: {json.dumps(event)}\n\n"

        # If already terminal, stop after replay
        if run["status"] in ("completed", "failed", "aborted"):
            return

        # Stream live events
        while True:
            try:
                event = await asyncio.wait_for(my_queue.get(), timeout=25.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("completed", "failed", "aborted", "paused"):
                    break
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        # Clean up this subscriber's queue
        try:
            run.get("_subscribers", []).remove(my_queue)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Stage payload builder
# ---------------------------------------------------------------------------

def _stage_payload(stage: str, state: dict) -> dict:
    base: dict[str, Any] = {
        "stage": stage,
        "coverage_iterations": state.get("coverage_iterations", 0),
        "test_pass_iterations": state.get("test_pass_iterations", 0),
    }
    if stage == "input_validated":
        base["project_path"] = state.get("project_path", "")

    elif stage == "junit_generated":
        base["generated_files"] = state.get("generated_test_files", [])
        base["generation_errors"] = state.get("generation_errors", [])
        base["file_count"] = len(state.get("generated_test_files", []))

    elif stage == "junit_validated":
        base["validation_results"] = state.get("validation_results", [])
        base["fixed_count"] = state.get("validation_fixed_count", 0)
        base["invalid_count"] = state.get("validation_invalid_count", 0)

    elif stage in ("compilation_passed", "compilation_failed"):
        base["success"] = state.get("compilation_success", False)
        base["report"] = state.get("compilation_report", [])
        base["fixed_count"] = state.get("compilation_fixed_count", 0)
        base["unfixed_count"] = state.get("compilation_unfixed_count", 0)
        base["raw_output"] = state.get("compilation_raw_output", "")[-3000:]

    elif "coverage" in stage:
        cov = state.get("coverage_data", {})
        base["coverage_percentage"] = cov.get("coverage_percentage", 0)
        base["coverage_threshold"] = state.get("coverage_threshold", 80)
        base["coverage_met"] = state.get("coverage_met", False)
        base["low_coverage_classes"] = cov.get("low_coverage_classes", [])
        base["iterations"] = state.get("coverage_iterations", 0)
        base["analysis"] = cov.get("analysis", {})

    elif "tests" in stage:
        surefire = state.get("execution_report", {}).get("surefire", {})
        base["total"] = surefire.get("total", 0)
        base["passed"] = surefire.get("passed", 0)
        base["failed"] = surefire.get("failed", 0)
        base["pass_rate"] = surefire.get("pass_rate", 0)
        base["pass_threshold"] = state.get("pass_threshold", 80)
        base["tests_passed"] = state.get("tests_passed", False)
        base["failed_tests"] = surefire.get("failed_tests", [])
        base["iterations"] = state.get("test_pass_iterations", 0)

    elif stage == "done":
        base["final_report"] = state.get("final_report", {})

    return base


# ---------------------------------------------------------------------------
# Pipeline runner (blocking — runs in thread pool)
# ---------------------------------------------------------------------------

def _run_pipeline(run_id: str, loop: asyncio.AbstractEventLoop) -> None:
    run = store.get(run_id)
    if not run:
        return

    run["_loop"] = loop
    run["status"] = "running"

    pipeline = build_pipeline()
    config = {"configurable": {"thread_id": run_id}}
    run["pipeline"] = pipeline
    run["config"] = config

    initial_state: PipelineState = run["state"]

    _push_event(run, "started", {
        "stage": "starting",
        "message": "Pipeline started",
        "project_path": initial_state.get("project_path", ""),
        "llm": f"{settings.LLM_PROVIDER} / {settings.LLM_MODEL}",
        "coverage_threshold": initial_state.get("coverage_threshold", 80),
        "pass_threshold": initial_state.get("pass_threshold", 80),
    })

    try:
        for event_state in pipeline.stream(initial_state, config=config, stream_mode="values"):
            run["state"] = event_state
            stage = event_state.get("stage", "")
            payload = _stage_payload(stage, event_state)
            _push_event(run, "stage_update", payload)

            # Check for human-loop interrupt
            if _is_interrupted(pipeline, config):
                run["status"] = "paused"
                run["human_loop_reason"] = event_state.get("human_loop_reason", "")
                _push_event(run, "paused", {
                    "stage": stage,
                    "reason": run["human_loop_reason"],
                    "compilation_report": event_state.get("compilation_report", []),
                    "compilation_raw_output": event_state.get("compilation_raw_output", "")[-3000:],
                    "coverage_data": event_state.get("coverage_data", {}),
                    "execution_report": event_state.get("execution_report", {}),
                    "message": "Pipeline paused — human review required",
                })
                return

        _finish_run(run)

    except Exception as e:
        run["status"] = "failed"
        run["error"] = str(e)
        _push_event(run, "failed", {
            "stage": run["stage"],
            "error": str(e),
            "message": f"Pipeline error: {e}",
        })


def _resume_pipeline(run_id: str, loop: asyncio.AbstractEventLoop) -> None:
    run = store.get(run_id)
    if not run:
        return

    run["_loop"] = loop
    pipeline = run["pipeline"]
    config = run["config"]

    try:
        for event_state in pipeline.stream(None, config=config, stream_mode="values"):
            run["state"] = event_state
            stage = event_state.get("stage", "")
            payload = _stage_payload(stage, event_state)
            _push_event(run, "stage_update", payload)

            if _is_interrupted(pipeline, config):
                run["status"] = "paused"
                run["human_loop_reason"] = event_state.get("human_loop_reason", "")
                _push_event(run, "paused", {
                    "stage": stage,
                    "reason": run["human_loop_reason"],
                    "message": "Pipeline paused again — human review required",
                })
                return

        _finish_run(run)

    except Exception as e:
        run["status"] = "failed"
        run["error"] = str(e)
        _push_event(run, "failed", {
            "stage": run["stage"],
            "error": str(e),
            "message": str(e),
        })


def _is_interrupted(pipeline, config: dict) -> bool:
    try:
        snapshot = pipeline.get_state(config)
        return "human_loop" in (snapshot.next or [])
    except Exception:
        return False


def _finish_run(run: dict) -> None:
    final_state = run["state"]
    stage = final_state.get("stage", "")
    if stage == "done":
        report = final_state.get("final_report", {})
        run["status"] = "completed"
        run["final_report"] = report
        run["completed_at"] = _now()
        _push_event(run, "completed", {
            "stage": "done",
            "final_report": report,
            "message": "Pipeline completed successfully",
        })
    else:
        run["status"] = "failed"
        run["error"] = final_state.get("error", f"Stopped at stage: {stage}")
        run["completed_at"] = _now()
        _push_event(run, "failed", {
            "stage": stage,
            "error": run["error"],
            "message": run["error"],
        })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["System"])
async def health():
    """Health check — returns LLM config."""
    return {
        "status": "ok",
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
    }


@app.get("/api/runs", tags=["Runs"])
async def list_runs():
    """List all pipeline runs."""
    return {"runs": store.list_runs()}


@app.post("/api/pipeline/run", tags=["Pipeline"], status_code=202)
async def start_run(req: RunRequest, background_tasks: BackgroundTasks):
    """
    Start a new pipeline run.

    Returns a `run_id`. Use it to:
    - Stream events:  GET /api/pipeline/{run_id}/stream
    - Check status:   GET /api/pipeline/{run_id}/status
    - Get report:     GET /api/pipeline/{run_id}/report
    """
    if not req.project_path and not req.bitbucket_repo_url:
        raise HTTPException(400, "Provide project_path or bitbucket_repo_url")

    run_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()

    initial_state: PipelineState = {
        "project_path": req.project_path,
        "input_source": "bitbucket" if req.bitbucket_repo_url else "directory",
        "bitbucket_repo_url": req.bitbucket_repo_url,
        "coverage_threshold": req.coverage_threshold,
        "pass_threshold": req.pass_threshold,
        "max_coverage_iterations": req.max_coverage_iterations,
        "max_test_pass_iterations": req.max_test_pass_iterations,
        "maven_cmd": req.maven_cmd,
    }

    run = store.create(run_id, initial_state)
    run["_loop"] = loop

    background_tasks.add_task(
        loop.run_in_executor, _executor, _run_pipeline, run_id, loop
    )

    return {
        "run_id": run_id,
        "status": "pending",
        "stream_url": f"/api/pipeline/{run_id}/stream",
        "status_url": f"/api/pipeline/{run_id}/status",
        "message": "Pipeline started. Connect to stream_url for real-time events.",
    }


@app.get("/api/pipeline/{run_id}/stream", tags=["Pipeline"])
async def stream_events(run_id: str):
    """
    Server-Sent Events stream for a pipeline run.

    Each event is a JSON object:
    ```
    data: {"type": "stage_update", "timestamp": "...", "data": {...}}
    ```

    Event types:
    - `started`      — pipeline kicked off
    - `stage_update` — a stage completed (see data.stage)
    - `paused`       — waiting for human review
    - `resumed`      — human approved, continuing
    - `completed`    — pipeline finished successfully
    - `failed`       — pipeline encountered an unrecoverable error
    - `aborted`      — pipeline was manually aborted
    """
    if not store.get(run_id):
        raise HTTPException(404, f"Run {run_id} not found")
    return StreamingResponse(
        _sse_generator(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/pipeline/{run_id}/status", tags=["Pipeline"])
async def get_status(run_id: str):
    """Get the current status and stage of a pipeline run."""
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")

    state = run["state"]
    return {
        "run_id": run_id,
        "status": run["status"],
        "stage": run["stage"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "human_loop_reason": run.get("human_loop_reason"),
        "error": run.get("error"),
        "coverage_iterations": state.get("coverage_iterations", 0),
        "test_pass_iterations": state.get("test_pass_iterations", 0),
        "generated_files": state.get("generated_test_files", []),
        "compilation_success": state.get("compilation_success"),
        "coverage_percentage": state.get("coverage_data", {}).get("coverage_percentage"),
        "pass_rate": state.get("execution_report", {}).get("surefire", {}).get("pass_rate"),
    }


@app.get("/api/pipeline/{run_id}/report", tags=["Pipeline"])
async def get_report(run_id: str):
    """
    Get the final pipeline report.
    Only available when status is `completed`.
    """
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    if run["status"] != "completed":
        raise HTTPException(400, f"Run not completed yet (status: {run['status']})")
    return run["final_report"]


@app.get("/api/pipeline/{run_id}/events", tags=["Pipeline"])
async def get_events(run_id: str):
    """Get the full event history for a run (non-streaming)."""
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    return {"run_id": run_id, "events": run["events"]}


@app.post("/api/pipeline/{run_id}/resume", tags=["Pipeline"])
async def resume_run(run_id: str, req: ResumeRequest, background_tasks: BackgroundTasks):
    """
    Resume a paused pipeline after human review.

    Set `approved: false` to abort the pipeline.
    Optionally include `feedback` notes.
    """
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    if run["status"] != "paused":
        raise HTTPException(400, f"Run is not paused (status: {run['status']})")

    pipeline = run["pipeline"]
    config = run["config"]

    pipeline.update_state(
        config,
        {
            "human_approved": req.approved,
            "human_feedback": req.feedback,
            "stage": "human_reviewed",
        },
    )

    if not req.approved:
        run["status"] = "aborted"
        run["completed_at"] = _now()
        _push_event(run, "aborted", {
            "stage": "aborted",
            "message": "Pipeline aborted by user",
        })
        return {"run_id": run_id, "status": "aborted"}

    run["status"] = "running"
    _push_event(run, "resumed", {
        "stage": "resuming",
        "message": "Pipeline resumed after human review",
        "feedback": req.feedback,
    })

    loop = asyncio.get_event_loop()
    background_tasks.add_task(
        loop.run_in_executor, _executor, _resume_pipeline, run_id, loop
    )

    return {
        "run_id": run_id,
        "status": "running",
        "message": "Pipeline resumed",
    }


@app.delete("/api/pipeline/{run_id}", tags=["Pipeline"])
async def abort_run(run_id: str):
    """Abort a running or paused pipeline run."""
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    if run["status"] in ("completed", "failed", "aborted"):
        raise HTTPException(400, f"Run already terminal (status: {run['status']})")

    run["status"] = "aborted"
    run["completed_at"] = _now()
    _push_event(run, "aborted", {"stage": "aborted", "message": "Run aborted"})
    return {"run_id": run_id, "status": "aborted"}
