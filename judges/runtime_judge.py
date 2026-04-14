import os
import subprocess
from typing import Sequence

from judges.base_judge import BaseJudge


class RuntimeJudge(BaseJudge):
    """
    Evaluates generated code inside a run directory and optionally keeps the candidate artifact.
    """

    def __init__(
        self,
        run_dir: str,
        command: Sequence[str] | str,
        target_score_regex: str | None = None,
        generated_filename: str = "candidate_solution.py",
        cleanup_generated: bool = False,
    ):
        self.run_dir = os.path.abspath(run_dir)
        self.command = command
        self.target_score_regex = target_score_regex
        self.generated_filename = generated_filename
        self.cleanup_generated = cleanup_generated
        os.makedirs(self.run_dir, exist_ok=True)

    def evaluate(self, output: str):
        cleaned_code = output.replace("```python", "").replace("```", "").strip()
        candidate_path = os.path.join(self.run_dir, self.generated_filename)

        with open(candidate_path, "w", encoding="utf-8") as handle:
            handle.write(cleaned_code)

        try:
            cmd_list = list(self.command) if not isinstance(self.command, str) else [self.command, self.generated_filename]
            result = subprocess.run(
                cmd_list,
                cwd=self.run_dir,
                capture_output=True,
                text=True,
                timeout=45,
            )
            stdout = result.stdout
            stderr = result.stderr
            return_code = result.returncode
            full_logs = f"--- STDOUT ---\n{stdout}\n--- STDERR ---\n{stderr}"
        except subprocess.TimeoutExpired:
            return False, 0.0, "Execution timed out after 45 seconds."
        finally:
            if self.cleanup_generated and os.path.exists(candidate_path):
                os.remove(candidate_path)

        if return_code != 0:
            return False, 0.0, f"Script failed with Return Code {return_code}.\n{full_logs}"

        score = 1.0
        if self.target_score_regex and stdout:
            import re

            match = re.search(self.target_score_regex, stdout)
            if match:
                try:
                    score = float(match.group(1))
                except ValueError:
                    score = 0.0

        return True, score, f"Execution successful.\n{full_logs}"
