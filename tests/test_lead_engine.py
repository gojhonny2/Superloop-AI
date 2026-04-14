import json
import os
import unittest
from uuid import uuid4

from core.lead_engine import LeadSuperloopEngine
from models.base_model import BaseModel


class SequenceModel(BaseModel):
    def __init__(self, name: str, responses: list[str]):
        super().__init__(name)
        self.responses = responses
        self.index = 0

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if self.index >= len(self.responses):
            return self.responses[-1]
        response = self.responses[self.index]
        self.index += 1
        return response


class LeadEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_owns_canonical_solution_and_finishes_with_verification(self):
        workspace_dir = os.path.join(os.getcwd(), "tests_runtime", f"lead_engine_case_{uuid4().hex[:8]}")
        os.makedirs(workspace_dir, exist_ok=True)

        data_path = os.path.join(workspace_dir, "data.csv")
        with open(data_path, "w", encoding="utf-8") as handle:
            handle.write("price\n100\n101\n102\n")

        manager = SequenceModel(
            "manager",
            [
                """
import candidate_solution

def run_judge():
    score = candidate_solution.calculate_strategy_score()
    print(f"FINAL_SCORE: {score}")

if __name__ == "__main__":
    run_judge()
""",
                """
def calculate_strategy_score():
    return 0.35
""",
                """
def calculate_strategy_score():
    return 0.84
""",
                """
def calculate_strategy_score():
    return 0.88
""",
            ],
        )
        specialist_a = SequenceModel(
            "specialist-a",
            [
                """
def calculate_strategy_score():
    return 0.70
""",
                """
def calculate_strategy_score():
    return 0.86
""",
            ],
        )
        specialist_b = SequenceModel(
            "specialist-b",
            [
                """
def calculate_strategy_score():
    return 0.78
""",
                """
def calculate_strategy_score():
    return 0.83
""",
            ],
        )

        directives = [["Prefer stable logic over flashy changes"], []]

        def read_directives():
            return directives.pop(0) if directives else []

        engine = LeadSuperloopEngine(
            manager=manager,
            specialists=[specialist_a, specialist_b],
            workspace_dir=workspace_dir,
        )

        result = await engine.run(
            goal="Build a candidate that reaches 0.80 score.",
            data_path=data_path,
            target_score=0.8,
            max_iterations=2,
            judge_brief="Trust the printed score and reject syntax errors.",
            operator_brief="Start simple, then harden.",
            directive_reader=read_directives,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "completed")
        self.assertGreaterEqual(result["score"], 0.8)
        self.assertTrue(os.path.exists(os.path.join(result["run_dir"], "candidate_solution_verified.py")))

        summary_path = os.path.join(result["run_dir"], "summary.json")
        with open(summary_path, "r", encoding="utf-8") as handle:
            summary = json.load(handle)

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["run_id"], result["run_id"])
