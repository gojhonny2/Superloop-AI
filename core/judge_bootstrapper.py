import ast
from itertools import islice
from typing import Optional

from models.base_model import BaseModel


class JudgeBootstrapper:
    """
    Uses the manager model to build the scoring script that becomes the run's source of truth.
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
    ) -> str:
        sample = self._get_data_sample(data_path)
        input_section = (
            f"""
EVALUATION INPUT PATH:
{data_path}

EVALUATION INPUT SAMPLE:
{sample}
""".strip()
            if data_path
            else """
EVALUATION INPUT PATH:
No external evaluation file was provided.

EVALUATION INPUT SAMPLE:
No external data sample is available. Build the judge around the workspace, generated candidate behavior, built-in tests, or benchmark logic implied by the user goal.
""".strip()
        )
        execution_rule = (
            f"2. Run the candidate against the real evaluation input at {data_path}."
            if data_path
            else "2. Evaluate the candidate using the workspace, candidate behavior, tests, or benchmark logic implied by the mission."
        )
        prompt = f"""
You are writing the scoring script for Superloop.

USER GOAL:
{goal_description}

JUDGE BRIEF:
{judge_brief or "No extra judge brief provided."}

{input_section}

Write a Python file named judge_logic.py.
It must:
1. Load {existing_candidate_name}.
{execution_rule}
3. Print FINAL_SCORE: <float>.
4. Print useful errors when evaluation fails.

Output only Python code.
""".strip()

        judge_code = self.orchestrator.generate(prompt=prompt, system_prompt="You are a senior test engineer.")
        cleaned = self.clean_code(judge_code)
        self.validate_judge(cleaned)

        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(cleaned)

        return output_path

    @staticmethod
    def clean_code(raw_text: str) -> str:
        return raw_text.replace("```python", "").replace("```", "").strip()

    @staticmethod
    def validate_judge(code: str):
        ast.parse(code)
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
