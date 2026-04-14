"""
Per-AI persistent notebook.

Every role in the loop (manager, specialist A, specialist B) gets its own
markdown scratchpad that lives inside the run folder and survives across
rounds. The lead engine appends a structured entry after each turn so the
AI can recall what it tried and what the judge said. The AI reads its own
scratchpad via `load()` before every new turn.
"""

import os
from datetime import datetime, timezone
from threading import Lock


class AIMemoryStore:
    """Filesystem-backed per-role memory store for a single run."""

    def __init__(self, run_dir: str):
        self.memory_dir = os.path.join(run_dir, "memory")
        os.makedirs(self.memory_dir, exist_ok=True)
        self._lock = Lock()

    def _path(self, role_key: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in role_key.lower())
        return os.path.join(self.memory_dir, f"{safe}.md")

    def load(self, role_key: str) -> str:
        """Return the full scratchpad for this role, or a placeholder if empty."""
        path = self._path(role_key)
        if not os.path.isfile(path):
            return "No prior notes yet."
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
        except OSError:
            return "No prior notes yet."
        return content or "No prior notes yet."

    def append(
        self,
        role_key: str,
        iteration: int,
        approach: str = "",
        score: float | None = None,
        judge_feedback: str = "",
        next_try: str = "",
    ) -> None:
        """Append a structured round entry to this role's scratchpad."""
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = [f"## Round {iteration} — {timestamp}"]
        approach = (approach or "").strip()
        if approach:
            lines.append(f"- Approach: {approach}")
        if score is not None:
            try:
                lines.append(f"- Score: {float(score):.2f}")
            except (TypeError, ValueError):
                pass
        feedback = (judge_feedback or "").strip()
        if feedback:
            trimmed = feedback[:500].strip()
            lines.append(f"- Judge said: {trimmed}")
        next_try = (next_try or "").strip()
        if next_try:
            lines.append(f"- Still to try: {next_try}")

        block = "\n".join(lines) + "\n\n"
        with self._lock:
            with open(self._path(role_key), "a", encoding="utf-8") as handle:
                handle.write(block)

    def list_roles(self) -> list[str]:
        if not os.path.isdir(self.memory_dir):
            return []
        return sorted(
            os.path.splitext(name)[0]
            for name in os.listdir(self.memory_dir)
            if name.endswith(".md")
        )
