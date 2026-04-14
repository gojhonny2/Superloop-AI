import ast
import time
from itertools import islice
from typing import Optional

from models.base_model import BaseModel


class JudgeBootstrapper:
    """
    Uses the manager model to build the scoring script.
    Receives the full workspace analysis so the judge understands every file
    in the project before writing the scoring logic.
    """

    def __init__(self, orchestrator: BaseModel):
        self.orchestrator = orchestrator

    def bootstrap_judge(
        self,
        data_path: str,
        goal_description: str,
        judge_brief: str,
        output_path: str,
        existing_candidate_name: str = "candidate_solution.py",
        workspace_context: str = "",
        workspace_structure: str = "",
    ) -> str:
        sample = self._get_data_sample(data_path)

        input_section = (
            f"EVALUATION INPUT PATH:\n{data_path}\n\nEVALUATION INPUT SAMPLE:\n{sample}"
            if data_path
            else "EVALUATION INPUT PATH:\nNo external evaluation file provided.\n\n"
                 "EVALUATION INPUT SAMPLE:\nNo external data. Build the judge around the existing project code, "
                 "its behavior, tests, or benchmark logic implied by the user goal."
        )

        execution_rule = (
            f"2. Run the candidate against the real evaluation input at {data_path}."
            if data_path
            else "2. Evaluate the candidate using the existing project code, tests, or benchmark logic implied by the goal."
        )

        workspace_section = ""
        if workspace_structure:
            workspace_section += f"\n\nWORKSPACE STRUCTURE (every folder and file in the project):\n{workspace_structure}\n"
        if workspace_context:
            workspace_section += (
                "\n\nWORKSPACE CODE (every file the judge needs to understand).\n"
                "Files are listed NEWEST-FIRST — the most recently modified files appear at the top. "
                "Read them in this order: the top files reflect the latest intent of the project, "
                "and older files toward the bottom provide the surrounding history.\n"
                f"{workspace_context}\n"
            )

        prompt = f"""
You are writing the scoring script for Superloop — an autonomous coding loop.

USER GOAL:
{goal_description}

JUDGE BRIEF:
{judge_brief or "No extra judge brief provided."}

{input_section}
{workspace_section}

CRITICAL RULES:
- You have been given the COMPLETE workspace above — every folder, every file, every line of code.
- The workspace contains an EXISTING project. The candidate file ({existing_candidate_name}) will contain code that modifies or extends this existing project.
- Your judge must understand what the existing project does and score the candidate based on whether it ACTUALLY achieves the user's goal.
- Do NOT generate synthetic/random test data when real data or real project code exists.
- Do NOT invent fake interfaces. Read the actual code above and use the real classes, functions, and modules.

PATH HANDLING (READ CAREFULLY — THIS IS WHERE MOST JUDGES FAIL):
- The judge is executed inside an isolated run directory. Its `__file__`, its `cwd`, and `os.pardir` all point INSIDE that isolated directory — NOT inside the workspace.
- To reach the workspace on disk, you MUST use the environment variable `SUPERLOOP_WORKSPACE_DIR`. The runtime sets it for you. Example:
      import os
      WORKSPACE = os.environ.get("SUPERLOOP_WORKSPACE_DIR") or os.getcwd()
- Build every workspace path from that root (e.g. `os.path.join(WORKSPACE, "some/subdir/data.csv")`). NEVER use `os.path.dirname(__file__)` or `os.pardir` to locate workspace files.
- The workspace is also on PYTHONPATH, so `import <workspace_package>` works for importing project modules.
- The candidate file itself (`{existing_candidate_name}`) IS next to the judge — load it from `os.path.dirname(__file__)`.

Write a Python file named judge_logic.py.
It must:
1. Load {existing_candidate_name} (same directory as judge_logic.py).
{execution_rule}
3. Print FINAL_SCORE: <float between 0.0 and 1.0>.
4. Print useful diagnostic info. Never silently swallow exceptions.
5. If the candidate crashes or has import errors, score must be 0.0 with clear error output.

Output only Python code.
""".strip()

        system_prompt = (
            "You are a senior test engineer. You have read the ENTIRE workspace codebase above. "
            "Write a judge that tests the candidate against the REAL project — not fake data."
        )

        judge_code = ""
        attempts = 3
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                judge_code = self.orchestrator.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                )
            except Exception as exc:
                last_error = exc
                judge_code = ""
            if judge_code and str(judge_code).strip():
                break
            if attempt < attempts:
                time.sleep(2 * attempt)

        if not judge_code or not str(judge_code).strip():
            suffix = f" Last transport error: {last_error}." if last_error else ""
            raise RuntimeError(
                f"Provider returned empty content for judge generation from "
                f"'{self.orchestrator.model_name}' after {attempts} attempts. "
                "This is usually transient (rate limit, content filter, or cold start) — "
                "retrying the run often works. If it keeps happening, try another provider key."
                f"{suffix}"
            )
        cleaned = self.clean_code(judge_code)
        try:
            self.validate_judge(cleaned)
        except ValueError:
            # Persist the raw output next to the intended judge path so the
            # failure is inspectable instead of lost.
            debug_path = output_path + ".raw.txt"
            try:
                with open(debug_path, "w", encoding="utf-8") as handle:
                    handle.write(cleaned)
            except OSError:
                pass
            raise

        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(cleaned)

        return output_path

    @staticmethod
    def clean_code(raw_text: str) -> str:
        return raw_text.replace("```python", "").replace("```", "").strip()

    @staticmethod
    def validate_judge(code: str):
        try:
            ast.parse(code)
        except SyntaxError as exc:
            preview = (code[:240] + "...") if len(code) > 240 else code
            raise ValueError(
                f"Manager returned text that is not valid Python (SyntaxError: {exc}). "
                f"Response preview: {preview!r}"
            ) from exc
        if "FINAL_SCORE" not in code:
            raise ValueError("Bootstrapped judge must emit FINAL_SCORE.")

    @staticmethod
    def _get_data_sample(data_path: str, lines: int = 10) -> str:
        if not data_path:
            return "NO EXTERNAL DATA OR BENCHMARK INPUT PROVIDED."
        try:
            with open(data_path, "r", encoding="utf-8") as handle:
                return "".join(islice(handle, lines)).strip()
        except OSError as exc:
            return f"FAILED TO READ DATA SAMPLE: {exc}"
