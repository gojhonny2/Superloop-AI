import json
import os
from typing import Any


class RunMemory:
    """
    Reads previous run summaries so resume mode stays explicit and predictable.
    """

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.runs_dir = os.path.join(self.workspace_dir, "runs")

    def list_runs(self) -> list[dict[str, Any]]:
        if not os.path.isdir(self.runs_dir):
            return []

        summaries: list[dict[str, Any]] = []
        for name in os.listdir(self.runs_dir):
            run_dir = os.path.join(self.runs_dir, name)
            summary_path = os.path.join(run_dir, "summary.json")
            if not os.path.isdir(run_dir) or not os.path.exists(summary_path):
                continue

            try:
                with open(summary_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                    payload["run_id"] = payload.get("run_id", name)
                    payload["run_dir"] = run_dir
                    summaries.append(payload)
            except (OSError, json.JSONDecodeError):
                continue

        summaries.sort(key=lambda item: item.get("finished_at") or item.get("started_at") or "", reverse=True)
        return summaries

    def get_resume_material(self, resume: bool = False, run_id: str | None = None) -> dict[str, Any]:
        if not resume:
            return {
                "summary": "Resume disabled for this run.",
                "best_code": "",
                "best_score": 0.0,
                "run_id": None,
            }

        summaries = self.list_runs()
        if run_id:
            summaries = [item for item in summaries if item.get("run_id") == run_id]

        if not summaries:
            return {
                "summary": "No previous runs available to resume from.",
                "best_code": "",
                "best_score": 0.0,
                "run_id": None,
            }

        latest = summaries[0]
        candidate_path = latest.get("verified_path") or latest.get("candidate_path")
        best_code = self._read_text(candidate_path) if candidate_path else ""
        summary_lines = [
            f"Previous run: {latest.get('run_id')}",
            f"Best score: {latest.get('score', 0.0)}",
            f"Status: {latest.get('status', 'unknown')}",
            f"Goal: {latest.get('goal', 'N/A')}",
        ]

        return {
            "summary": "\n".join(summary_lines),
            "best_code": best_code,
            "best_score": latest.get("score", 0.0),
            "run_id": latest.get("run_id"),
        }

    def _read_text(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return ""
