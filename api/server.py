import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections import Counter

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.lead_engine import LeadSuperloopEngine
from core.run_context import create_run_id
from core.run_memory import RunMemory
from models.runtime_factory import RuntimeModelFactory
from superloop_version import __version__, RELEASE_CHANNEL

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


class RunRequest(BaseModel):
    goal: str = Field(default_factory=lambda: get_runtime_defaults()["goal"])
    workspace_path: str = Field(default_factory=lambda: get_runtime_defaults()["workspace_path"])
    data_path: str = Field(default_factory=lambda: get_runtime_defaults()["data_path"])
    judge_brief: str = Field(default_factory=lambda: get_runtime_defaults()["judge_brief"])
    operator_brief: str = Field(default_factory=lambda: get_runtime_defaults()["operator_brief"])
    target_score: float = Field(default_factory=lambda: get_runtime_defaults()["target_score"])
    max_iterations: int = Field(default_factory=lambda: get_runtime_defaults()["max_iterations"])
    resume: bool = Field(default_factory=lambda: get_runtime_defaults()["resume"])
    manager_descriptor: str = Field(default_factory=lambda: get_runtime_defaults()["manager_descriptor"])
    specialist_descriptors: list[str] = Field(default_factory=lambda: list(get_runtime_defaults()["specialist_descriptors"]))


class DirectiveRequest(BaseModel):
    message: str


class WorkspaceAnalyzeRequest(BaseModel):
    workspace_path: str = "."
    data_path: str = ""


@dataclass
class LiveRun:
    run_id: str
    workspace_path: str
    status: str = "queued"
    summary: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    pending_directives: list[str] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None

    def publish(self, event: dict[str, Any]):
        self.events.append(event)
        self.queue.put_nowait(event)

    def consume_directives(self) -> list[str]:
        directives = list(self.pending_directives)
        self.pending_directives.clear()
        return directives

    def add_directive(self, message: str):
        self.pending_directives.append(message)
        self.publish(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": "directive.queued",
                "actor": "human",
                "message": message,
                "score": None,
                "artifacts": {},
                "meta": {},
            }
        )

    @property
    def done(self) -> bool:
        return self.status in {"completed", "completed_with_warnings", "stopped", "failed"}


RUNS: dict[str, LiveRun] = {}

app = FastAPI(title="Superloop AI", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

WORKSPACE_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".json": "JSON",
    ".md": "Markdown",
    ".html": "HTML",
    ".css": "CSS",
    ".csv": "CSV",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
}
WORKSPACE_SKIP_DIRS = {".git", ".idea", ".vscode", ".venv", "venv", "__pycache__", "node_modules", "runs", "tests_runtime"}


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _split_specialists(raw: str | None) -> list[str]:
    if not raw:
        return ["openai:gpt-4o", "anthropic:claude-3-5-sonnet-20240620"]
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or ["openai:gpt-4o", "anthropic:claude-3-5-sonnet-20240620"]


def _normalize_specialists(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or _split_specialists(None)
    return _split_specialists(str(value) if value is not None else None)


def _runtime_defaults_from_env() -> dict[str, Any]:
    return {
        "goal": os.getenv("SUPERLOOP_GOAL", ""),
        "workspace_path": os.getenv("SUPERLOOP_WORKSPACE", "."),
        "data_path": os.getenv("SUPERLOOP_DATA_PATH", ""),
        "judge_brief": os.getenv("SUPERLOOP_JUDGE_BRIEF", ""),
        "operator_brief": os.getenv("SUPERLOOP_OPERATOR_BRIEF", ""),
        "target_score": _env_float("SUPERLOOP_TARGET_SCORE", 0.8),
        "max_iterations": _env_int("SUPERLOOP_MAX_ITERS", 5),
        "resume": _env_bool("SUPERLOOP_RESUME", False),
        "manager_descriptor": os.getenv("SUPERLOOP_MANAGER", "gemini:gemini-1.5-pro"),
        "specialist_descriptors": _split_specialists(os.getenv("SUPERLOOP_SPECIALISTS")),
    }


RUNTIME_DEFAULTS: dict[str, Any] = _runtime_defaults_from_env()


def configure_runtime_defaults(**overrides: Any):
    global RUNTIME_DEFAULTS
    updated = dict(_runtime_defaults_from_env())
    updated.update({key: value for key, value in overrides.items() if value is not None})
    updated["specialist_descriptors"] = _normalize_specialists(updated.get("specialist_descriptors"))
    RUNTIME_DEFAULTS = updated


def get_runtime_defaults() -> dict[str, Any]:
    defaults = dict(RUNTIME_DEFAULTS)
    specialists = list(defaults.get("specialist_descriptors", []))
    defaults["specialist_descriptors"] = specialists
    defaults["specialist_1_descriptor"] = specialists[0] if len(specialists) > 0 else ""
    defaults["specialist_2_descriptor"] = specialists[1] if len(specialists) > 1 else ""
    return defaults


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__, "channel": RELEASE_CHANNEL}


@app.get("/api/runtime-defaults")
async def runtime_defaults():
    return {
        "defaults": get_runtime_defaults(),
        "version": __version__,
        "channel": RELEASE_CHANNEL,
    }


@app.get("/api/browse")
async def browse_directory(path: str = "."):
    resolved = os.path.abspath(path or ".")
    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail="Path not found.")
    if not os.path.isdir(resolved):
        resolved = os.path.dirname(resolved)

    parent = os.path.dirname(resolved)
    entries: list[dict[str, Any]] = []

    try:
        for name in sorted(os.listdir(resolved)):
            if name.startswith("."):
                continue
            full = os.path.join(resolved, name)
            is_dir = os.path.isdir(full)
            entries.append({
                "name": name,
                "path": full.replace("\\", "/"),
                "is_dir": is_dir,
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied.")

    # Also list available drives on Windows
    drives: list[str] = []
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive.replace("\\", "/"))

    return {
        "current": resolved.replace("\\", "/"),
        "parent": parent.replace("\\", "/") if parent != resolved else None,
        "entries": entries,
        "drives": drives,
    }


@app.post("/api/workspace/analyze")
async def analyze_workspace(request: WorkspaceAnalyzeRequest):
    workspace_path = os.path.abspath(request.workspace_path or ".")
    if not os.path.exists(workspace_path):
        raise HTTPException(status_code=404, detail="Workspace path not found.")
    if not os.path.isdir(workspace_path):
        raise HTTPException(status_code=400, detail="Workspace path must be a directory.")

    stats = _scan_workspace(workspace_path, request.data_path)
    return {"workspace": stats}


@app.post("/api/runs")
async def create_run(request: RunRequest):
    try:
        manager = RuntimeModelFactory.create_model(request.manager_descriptor)
        specialists = [RuntimeModelFactory.create_model(item) for item in request.specialist_descriptors]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    engine = LeadSuperloopEngine(manager=manager, specialists=specialists, workspace_dir=request.workspace_path)
    run_id = create_run_id()
    handle = LiveRun(run_id=run_id, workspace_path=os.path.abspath(request.workspace_path))

    async def runner():
        try:
            handle.status = "running"
            result = await engine.run(
                goal=request.goal,
                data_path=request.data_path,
                max_iterations=request.max_iterations,
                target_score=request.target_score,
                judge_brief=request.judge_brief,
                operator_brief=request.operator_brief,
                resume=request.resume,
                run_id=run_id,
                event_callback=handle.publish,
                directive_reader=handle.consume_directives,
            )
            handle.run_id = result["run_id"]
            handle.summary = result
            handle.status = result["status"]
            RUNS[handle.run_id] = handle
        except Exception as exc:
            handle.status = "failed"
            handle.summary = {"success": False, "status": "failed", "error": str(exc)}
            handle.publish(
                {
                    "timestamp": "",
                    "phase": "run.failed",
                    "actor": "system",
                    "message": str(exc),
                    "score": None,
                    "artifacts": {},
                    "meta": {},
                }
            )

    handle.task = asyncio.create_task(runner())
    RUNS[handle.run_id] = handle
    return {"run_id": handle.run_id, "status": handle.status}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    handle = RUNS.get(run_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Run not found.")

    return {
        "run_id": run_id,
        "status": handle.status,
        "summary": handle.summary,
        "events": handle.events[-150:],
        "pending_directives": len(handle.pending_directives),
        "metrics": _summarize_run_metrics(handle.events, handle.summary, handle.pending_directives),
    }


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str):
    handle = RUNS.get(run_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Run not found.")

    async def event_stream():
        for event in handle.events:
            yield f"data: {json.dumps(event)}\n\n"

        if handle.done:
            return

        while True:
            try:
                event = await asyncio.wait_for(handle.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                if handle.done:
                    break
                yield ": keep-alive\n\n"
                continue

            yield f"data: {json.dumps(event)}\n\n"
            if handle.done and handle.queue.empty():
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/directives")
async def push_directive(run_id: str, payload: DirectiveRequest):
    handle = RUNS.get(run_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Run not found.")

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Directive message cannot be empty.")

    handle.add_directive(message)
    return {"ok": True, "queued": len(handle.pending_directives)}


@app.get("/api/runs/{run_id}/artifacts")
async def list_artifacts(run_id: str):
    handle = RUNS.get(run_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Run not found.")

    run_dir = handle.summary.get("run_dir")
    if not run_dir or not os.path.isdir(run_dir):
        return {"artifacts": []}

    artifacts = []
    for root, _, files in os.walk(run_dir):
        for filename in sorted(files):
            path = os.path.join(root, filename)
            relative_path = os.path.relpath(path, run_dir).replace("\\", "/")
            artifacts.append(
                {
                    "name": filename,
                    "relative_path": relative_path,
                    "size": os.path.getsize(path),
                    "download_url": f"/api/runs/{run_id}/artifacts/{relative_path}",
                }
            )

    return {"artifacts": sorted(artifacts, key=lambda item: item["relative_path"])}


@app.get("/api/runs/{run_id}/artifacts/{artifact_path:path}")
async def get_artifact(run_id: str, artifact_path: str):
    handle = RUNS.get(run_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Run not found.")

    run_dir = handle.summary.get("run_dir")
    if not run_dir:
        raise HTTPException(status_code=404, detail="Run artifacts not available.")

    resolved = os.path.abspath(os.path.join(run_dir, artifact_path))
    if os.path.commonpath([resolved, os.path.abspath(run_dir)]) != os.path.abspath(run_dir):
        raise HTTPException(status_code=400, detail="Invalid artifact path.")
    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail="Artifact not found.")

    return FileResponse(resolved)


@app.get("/api/history")
async def history(workspace_path: str = "."):
    memory = RunMemory(workspace_path)
    return {"runs": memory.list_runs()}


def _scan_workspace(workspace_path: str, data_path: str) -> dict[str, Any]:
    total_files = 0
    relevant_files = 0
    total_size = 0
    directories = 0
    language_counter: Counter[str] = Counter()
    extension_counter: Counter[str] = Counter()
    sample_files: list[str] = []
    likely_entrypoints: list[str] = []
    detected_data_files: list[str] = []

    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [item for item in dirs if item not in WORKSPACE_SKIP_DIRS and not item.startswith(".")]
        directories += len(dirs)

        for filename in files:
            total_files += 1
            path = os.path.join(root, filename)
            relative = os.path.relpath(path, workspace_path).replace("\\", "/")
            suffix = Path(filename).suffix.lower()
            try:
                total_size += os.path.getsize(path)
            except OSError:
                pass

            if suffix in WORKSPACE_EXTENSIONS:
                relevant_files += 1
                extension_counter[suffix or "(none)"] += 1
                language_counter[WORKSPACE_EXTENSIONS[suffix]] += 1
                if len(sample_files) < 10:
                    sample_files.append(relative)

            lower_name = filename.lower()
            if lower_name in {"package.json", "pyproject.toml", "setup.py", "requirements.txt", "dockerfile", "readme.md"}:
                likely_entrypoints.append(relative)
            if suffix in {".csv", ".json", ".parquet", ".xlsx"} or "data" in lower_name:
                detected_data_files.append(relative)

    detected_data_files = sorted(set(detected_data_files))[:8]
    likely_entrypoints = sorted(set(likely_entrypoints))[:8]

    resolved_data_path = ""
    data_exists = False
    data_preview = ""
    if data_path:
        candidate = data_path if os.path.isabs(data_path) else os.path.join(workspace_path, data_path)
        resolved_data_path = os.path.abspath(candidate)
        data_exists = os.path.exists(resolved_data_path)
        if data_exists and os.path.isfile(resolved_data_path):
            try:
                with open(resolved_data_path, "r", encoding="utf-8") as handle:
                    data_preview = "".join(handle.readlines()[:6]).strip()
            except OSError:
                data_preview = ""

    return {
        "workspace_path": workspace_path,
        "exists": True,
        "total_files": total_files,
        "relevant_files": relevant_files,
        "directories": directories,
        "total_size_bytes": total_size,
        "languages": [{"name": key, "count": value} for key, value in language_counter.most_common(6)],
        "extensions": [{"name": key, "count": value} for key, value in extension_counter.most_common(8)],
        "sample_files": sample_files,
        "likely_entrypoints": likely_entrypoints,
        "detected_data_files": detected_data_files,
        "resolved_data_path": resolved_data_path,
        "data_exists": data_exists,
        "data_preview": data_preview,
    }


def _summarize_run_metrics(events: list[dict[str, Any]], summary: dict[str, Any], pending_directives: int) -> dict[str, Any]:
    candidate_events = [item for item in events if item.get("phase") == "candidate.evaluated"]
    latest_event = events[-1] if events else {}
    latest_iteration = 0
    best_score = summary.get("score")
    if best_score is None:
        numeric_scores = [item.get("score") for item in events if isinstance(item.get("score"), (int, float))]
        best_score = max(numeric_scores) if numeric_scores else 0.0

    branch_scores: list[dict[str, Any]] = []
    for item in candidate_events[-8:]:
        meta = item.get("meta") or {}
        latest_iteration = max(latest_iteration, meta.get("iteration", 0))
        role = meta.get("role", "")
        if role.startswith("specialist"):
            branch_scores.append(
                {
                    "actor": item.get("actor", "unknown"),
                    "score": item.get("score", 0.0) or 0.0,
                    "iteration": meta.get("iteration", 0),
                    "role": role,
                }
            )

    manager_scores = [
        {
            "actor": item.get("actor", "manager"),
            "score": item.get("score", 0.0) or 0.0,
            "iteration": (item.get("meta") or {}).get("iteration", 0),
            "role": (item.get("meta") or {}).get("role", ""),
        }
        for item in candidate_events
        if ((item.get("meta") or {}).get("role", "")).startswith("manager")
    ]

    return {
        "current_iteration": latest_iteration,
        "best_score": best_score or 0.0,
        "active_phase": latest_event.get("phase", "idle"),
        "pending_directives": pending_directives,
        "branch_scores": branch_scores[-4:],
        "manager_scores": manager_scores[-3:],
        "event_count": len(events),
        "artifact_count": len(summary.get("artifacts", {})) if summary else 0,
    }
