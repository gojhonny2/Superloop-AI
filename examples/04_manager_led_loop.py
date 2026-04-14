import asyncio
import os

from core.lead_engine import LeadSuperloopEngine
from models.base_model import BaseModel


class DemoModel(BaseModel):
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


async def main():
    workspace = os.path.join(os.getcwd(), "workspace")
    os.makedirs(workspace, exist_ok=True)
    data_path = os.path.join(workspace, "dummy_prices.csv")
    with open(data_path, "w", encoding="utf-8") as handle:
        handle.write("price\n100\n101\n102\n")

    manager = DemoModel(
        "demo-manager",
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
    return 0.40
""",
            """
def calculate_strategy_score():
    return 0.82
""",
            """
def calculate_strategy_score():
    return 0.88
""",
        ],
    )
    specialist_a = DemoModel(
        "demo-specialist-a",
        [
            """
def calculate_strategy_score():
    return 0.75
""",
            """
def calculate_strategy_score():
    return 0.86
""",
        ],
    )
    specialist_b = DemoModel(
        "demo-specialist-b",
        [
            """
def calculate_strategy_score():
    return 0.70
""",
            """
def calculate_strategy_score():
    return 0.84
""",
        ],
    )

    engine = LeadSuperloopEngine(manager=manager, specialists=[specialist_a, specialist_b], workspace_dir=workspace)
    result = await engine.run(
        goal="Build a candidate that scores above 0.80.",
        data_path=data_path,
        target_score=0.8,
        max_iterations=2,
        judge_brief="Trust the score only when the candidate is valid Python.",
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
