import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4


def create_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{timestamp}-{uuid4().hex[:8]}"


@dataclass
class RunPaths:
    workspace_dir: str
    run_id: str
    runs_dir: str
    run_dir: str
    judge_path: str
    candidate_path: str
    verified_path: str
    events_path: str
    research_log_path: str
    summary_path: str
    artifacts_dir: str

    @classmethod
    def create(cls, workspace_dir: str, run_id: str | None = None) -> "RunPaths":
        workspace_dir = os.path.abspath(workspace_dir)
        run_id = run_id or create_run_id()
        runs_dir = os.path.join(workspace_dir, "runs")
        run_dir = os.path.join(runs_dir, run_id)
        artifacts_dir = os.path.join(run_dir, "artifacts")

        os.makedirs(artifacts_dir, exist_ok=True)

        return cls(
            workspace_dir=workspace_dir,
            run_id=run_id,
            runs_dir=runs_dir,
            run_dir=run_dir,
            judge_path=os.path.join(run_dir, "judge_logic.py"),
            candidate_path=os.path.join(run_dir, "candidate_solution.py"),
            verified_path=os.path.join(run_dir, "candidate_solution_verified.py"),
            events_path=os.path.join(run_dir, "events.jsonl"),
            research_log_path=os.path.join(run_dir, "swarm_research_log.jsonl"),
            summary_path=os.path.join(run_dir, "summary.json"),
            artifacts_dir=artifacts_dir,
        )

    def to_dict(self) -> dict:
        return asdict(self)
