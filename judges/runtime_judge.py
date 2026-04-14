import os
import subprocess
from typing import Sequence

from judges.base_judge import BaseJudge


class RuntimeJudge(BaseJudge):
    """
    Evaluates generated code inside a run directory.
    Adds the workspace directory to PYTHONPATH so candidates can import project modules.
    """

    def __init__(
        self,
        run_dir: str,
        command: Sequence[str] | str,
        target_score_regex: str | None = None,
        generated_filename: str = "candidate_solution.py",
        cleanup_generated: bool = False,
        workspace_dir: str | None = None,
        timeout: int = 120,
    ):
        self.run_dir = os.path.abspath(run_dir)
        self.command = command
        self.target_score_regex = target_score_regex
        self.generated_filename = generated_filename
        self.cleanup_generated = cleanup_generated
        self.workspace_dir = os.path.abspath(workspace_dir) if workspace_dir else None
        self.timeout = timeout
        os.makedirs(self.run_dir, exist_ok=True)

    def evaluate(self, output: str):
        cleaned_code = output.replace("```python", "").replace("```", "").strip()
        candidate_path = os.path.join(self.run_dir, self.generated_filename)

        with open(candidate_path, "w", encoding="utf-8") as handle:
            handle.write(cleaned_code)

        # Build environment with workspace on PYTHONPATH and expose the
        # workspace root as an explicit env var so the judge can locate
        # project files no matter what app it's evaluating.
        env = os.environ.copy()
        if self.workspace_dir:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = self.workspace_dir + (os.pathsep + existing if existing else "")
            env["SUPERLOOP_WORKSPACE_DIR"] = self.workspace_dir

        try:
            cmd_list = list(self.command) if not isinstance(self.command, str) else [self.command, self.generated_filename]
            result = subprocess.run(
                cmd_list,
                cwd=self.run_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
            stdout = result.stdout
            stderr = result.stderr
            return_code = result.returncode
            full_logs = f"--- STDOUT ---\n{stdout}\n--- STDERR ---\n{stderr}"
        except subprocess.TimeoutExpired:
            return False, 0.0, f"Execution timed out after {self.timeout} seconds."
        finally:
            if self.cleanup_generated and os.path.exists(candidate_path):
                os.remove(candidate_path)

        if return_code != 0:
            return False, 0.0, f"Script failed with Return Code {return_code}.\n{full_logs}"

        score = 0.0
        if self.target_score_regex and stdout:
            import re

            match = re.search(self.target_score_regex, stdout)
            if match:
                try:
                    score = float(match.group(1))
                except ValueError:
                    score = 0.0
            else:
                # No FINAL_SCORE found — this is likely an error
                return False, 0.0, f"No FINAL_SCORE found in output.\n{full_logs}"

        return True, score, f"Execution successful.\n{full_logs}"
