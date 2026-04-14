import ast
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from core.judge_bootstrapper import JudgeBootstrapper
from core.run_context import RunPaths
from core.run_memory import RunMemory
from core.runtime_workspace import RuntimeWorkspace
from judges.runtime_judge import RuntimeJudge
from models.base_model import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class LeadSuperloopEngine:
    """
    Manager-led build and test loop.

    The manager owns the canonical solution, specialists explore branches, and
    the judge remains the truth source for every round.
    """

    def __init__(
        self,
        manager: BaseModel,
        specialists: list[BaseModel],
        workspace_dir: str,
    ):
        self.manager = manager
        self.specialists = specialists
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.workspace = RuntimeWorkspace(self.workspace_dir)
        self.memory = RunMemory(self.workspace_dir)
        self.bootstrapper = JudgeBootstrapper(manager)
        self.judge: RuntimeJudge | None = None
        self.run_paths: RunPaths | None = None
        self.research_trail: list[dict[str, Any]] = []
        self.event_callback: Callable[[dict[str, Any]], None] | None = None
        self.directive_reader: Callable[[], list[str]] | None = None
        self.started_at: str = ""

    def _build_round_history(self) -> str:
        """Build a concise summary of all completed rounds so every agent
        remembers what was tried, what worked, and what failed."""
        if not self.research_trail:
            return "No previous rounds yet."

        lines: list[str] = []
        for entry in self.research_trail:
            iteration = entry.get("iteration", "?")
            mode = entry.get("mode", "unknown")
            canonical_after = entry.get("canonical_after") or entry.get("canonical", {})
            score = canonical_after.get("score", 0.0)
            feedback = (canonical_after.get("feedback") or "")[:280]

            specialist_summaries = []
            for spec in entry.get("specialists", []):
                specialist_summaries.append(
                    f"  - {spec.get('actor', '?')}: score {spec.get('score', 0.0):.2f}"
                )

            lines.append(f"Round {iteration} ({mode}):")
            lines.append(f"  Canonical score after round: {score:.2f}")
            if specialist_summaries:
                lines.append("  Specialist scores:")
                lines.extend(specialist_summaries)
            lines.append(f"  Judge feedback summary: {feedback}")
            lines.append("")

        return "\n".join(lines)

    async def run(
        self,
        goal: str,
        data_path: str = "",
        max_iterations: int = 5,
        target_score: float = 0.8,
        judge_brief: str = "",
        operator_brief: str = "",
        resume: bool = False,
        run_id: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        directive_reader: Callable[[], list[str]] | None = None,
    ) -> dict[str, Any]:
        self.event_callback = event_callback
        self.directive_reader = directive_reader
        self.research_trail = []
        self.run_paths = RunPaths.create(self.workspace_dir, run_id=run_id)
        self.started_at = datetime.now(timezone.utc).isoformat()
        resume_material = self.memory.get_resume_material(resume=resume)

        self._emit(
            phase="run.started",
            actor="system",
            message="Superloop run created.",
            meta={"run_id": self.run_paths.run_id, "target_score": target_score},
        )

        try:
            self.bootstrapper.bootstrap_judge(
                data_path=data_path,
                goal_description=goal,
                judge_brief=judge_brief,
                output_path=self.run_paths.judge_path,
            )
            self._emit(
                phase="judge.ready",
                actor=self.manager.model_name,
                message="Manager created the judge logic.",
                artifacts={"judge_path": self.run_paths.judge_path},
            )

            self.judge = RuntimeJudge(
                run_dir=self.run_paths.run_dir,
                command=["python", "judge_logic.py"],
                target_score_regex=r"FINAL_SCORE:\s*(\d+\.?\d*)",
                generated_filename=os.path.basename(self.run_paths.candidate_path),
                cleanup_generated=False,
            )

            workspace_context = self.workspace.build_context()
            data_preview = self.workspace.data_preview(data_path)

            baseline_prompt = self._build_baseline_prompt(
                goal=goal,
                judge_brief=judge_brief,
                operator_brief=operator_brief,
                workspace_context=workspace_context,
                data_preview=data_preview,
                resume_material=resume_material,
            )
            baseline_code = self._generate_code(
                self.manager,
                baseline_prompt,
                "You are the lead AI engineer. Talk to the human through the brief, build the first version, and output only the full code.",
            )
            canonical = self._evaluate_candidate(
                iteration=0,
                actor=self.manager.model_name,
                role="manager-baseline",
                code=baseline_code,
                artifact_name="round_00_manager_baseline.py",
            )
            self._persist_canonical(canonical)
            self._record_round(
                {
                    "iteration": 0,
                    "mode": "baseline",
                    "canonical": canonical,
                    "specialists": [],
                    "operator_directives": [operator_brief] if operator_brief else [],
                }
            )

            verification_mode = canonical["score"] >= target_score
            iteration = 1

            if verification_mode:
                self._emit(
                    phase="verification.pending",
                    actor="system",
                    message="Baseline hit the target. Launching one verification and polish round.",
                    score=canonical["score"],
                    meta={"iteration": 0, "mode": "verification"},
                )

            while iteration <= max_iterations or verification_mode:
                round_is_verification = verification_mode
                directives = self._consume_directives()
                prompt_for_specialists = self._build_specialist_prompt(
                    goal=goal,
                    judge_brief=judge_brief,
                    canonical=canonical,
                    round_is_verification=round_is_verification,
                    directives=directives,
                )

                self._emit(
                    phase="specialists.requested",
                    actor=self.manager.model_name,
                    message=(
                        "Manager requested verification and polish from specialists."
                        if round_is_verification
                        else "Manager requested two optimization branches from specialists."
                    ),
                    score=canonical["score"],
                    meta={"iteration": iteration, "mode": "verification" if round_is_verification else "optimization"},
                )

                specialist_payloads = await asyncio.gather(
                    *[
                        self._generate_with_model(
                            model,
                            prompt_for_specialists,
                            (
                                "You are auditing the manager's result and the judge for accuracy. "
                                "Output only improved full code."
                                if round_is_verification
                                else "You are an optimization specialist. Improve the manager-owned code and output only full code."
                            ),
                        )
                        for model in self.specialists
                    ]
                )

                specialist_results: list[dict[str, Any]] = []
                for model, proposal in zip(self.specialists, specialist_payloads):
                    specialist_results.append(
                        self._evaluate_candidate(
                            iteration=iteration,
                            actor=model.model_name,
                            role="specialist-verification" if round_is_verification else "specialist-branch",
                            code=proposal,
                            artifact_name=f"round_{iteration:02d}_{self._slug(model.model_name)}.py",
                        )
                    )

                integration_prompt = self._build_manager_integration_prompt(
                    goal=goal,
                    judge_brief=judge_brief,
                    canonical=canonical,
                    specialist_results=specialist_results,
                    directives=directives,
                    round_is_verification=round_is_verification,
                )
                manager_candidate_code = self._generate_code(
                    self.manager,
                    integration_prompt,
                    (
                        "You are the lead AI engineer. Review both specialist branches, integrate the useful changes into the canonical solution, "
                        "and output only the full final code for this round."
                    ),
                )
                manager_result = self._evaluate_candidate(
                    iteration=iteration,
                    actor=self.manager.model_name,
                    role="manager-verification" if round_is_verification else "manager-integration",
                    code=manager_candidate_code,
                    artifact_name=f"round_{iteration:02d}_manager_integrated.py",
                )

                round_summary = {
                    "iteration": iteration,
                    "mode": "verification" if round_is_verification else "optimization",
                    "canonical_before": canonical,
                    "specialists": specialist_results,
                    "manager_result": manager_result,
                    "operator_directives": directives,
                }

                if manager_result["score"] >= canonical["score"]:
                    canonical = manager_result
                    self._emit(
                        phase="manager.canonical_updated",
                        actor=self.manager.model_name,
                        message="Manager adopted the integrated candidate as the new canonical solution.",
                        score=canonical["score"],
                        meta={"iteration": iteration, "mode": round_summary["mode"]},
                    )
                else:
                    self._emit(
                        phase="manager.canonical_retained",
                        actor=self.manager.model_name,
                        message="Manager kept the previous canonical solution because the integrated result regressed.",
                        score=canonical["score"],
                        meta={"iteration": iteration, "mode": round_summary["mode"]},
                    )

                self._persist_canonical(canonical)
                round_summary["canonical_after"] = canonical
                self._record_round(round_summary)

                if round_is_verification:
                    return self._finalize_run(
                        goal=goal,
                        target_score=target_score,
                        judge_brief=judge_brief,
                        canonical=canonical,
                        status="completed" if canonical["score"] >= target_score else "completed_with_warnings",
                    )

                if canonical["score"] >= target_score:
                    verification_mode = True
                    iteration += 1
                    self._emit(
                        phase="verification.pending",
                        actor="system",
                        message="Target score reached. Scheduling a final specialist audit and polish round.",
                        score=canonical["score"],
                        meta={"iteration": iteration, "mode": "verification"},
                    )
                else:
                    iteration += 1

            return self._finalize_run(
                goal=goal,
                target_score=target_score,
                judge_brief=judge_brief,
                canonical=canonical,
                status="stopped",
            )
        except Exception as exc:
            self._emit(
                phase="run.failed",
                actor="system",
                message=f"Run failed: {exc}",
            )
            fallback = {
                "actor": self.manager.model_name,
                "role": "manager-error",
                "content": "",
                "score": 0.0,
                "success": False,
                "feedback": str(exc),
                "artifact_path": "",
                "iteration": -1,
            }
            return self._finalize_run(
                goal=goal,
                target_score=target_score,
                judge_brief=judge_brief,
                canonical=fallback,
                status="failed",
            )

    async def _generate_with_model(self, model: BaseModel, prompt: str, system_prompt: str) -> str:
        self._emit(
            phase="model.generating",
            actor=model.model_name,
            message="Generating candidate for current round.",
        )
        return await asyncio.to_thread(model.generate, prompt, system_prompt)

    def _generate_code(self, model: BaseModel, prompt: str, system_prompt: str) -> str:
        raw = model.generate(prompt, system_prompt)
        return self._clean_code(raw)

    def _evaluate_candidate(
        self,
        iteration: int,
        actor: str,
        role: str,
        code: str,
        artifact_name: str,
    ) -> dict[str, Any]:
        artifact_path = os.path.join(self.run_paths.artifacts_dir, artifact_name)
        self.workspace.write_text(artifact_path, code)

        syntax_error = self._validate_python(code)
        if syntax_error:
            result = {
                "iteration": iteration,
                "actor": actor,
                "role": role,
                "content": code,
                "score": 0.0,
                "success": False,
                "feedback": f"Candidate is not valid Python: {syntax_error}",
                "artifact_path": artifact_path,
            }
        else:
            success, score, feedback = self.judge.evaluate(code)
            result = {
                "iteration": iteration,
                "actor": actor,
                "role": role,
                "content": code,
                "score": score,
                "success": success,
                "feedback": feedback,
                "artifact_path": artifact_path,
            }

        self._emit(
            phase="candidate.evaluated",
            actor=actor,
            message=f"{role} evaluated.",
            score=result["score"],
            artifacts={"artifact_path": artifact_path},
            meta={"iteration": iteration, "success": result["success"], "role": role},
        )
        return result

    def _persist_canonical(self, canonical: dict[str, Any]):
        self.workspace.write_text(self.run_paths.candidate_path, canonical["content"])

    def _record_round(self, round_summary: dict[str, Any]):
        self.research_trail.append(round_summary)
        self._export_research_log()

    def _export_research_log(self):
        lines = [json.dumps(entry) for entry in self.research_trail]
        self.workspace.write_text(self.run_paths.research_log_path, "\n".join(lines) + ("\n" if lines else ""))

    def _finalize_run(
        self,
        goal: str,
        target_score: float,
        judge_brief: str,
        canonical: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        finished_at = datetime.now(timezone.utc).isoformat()
        summary = {
            "run_id": self.run_paths.run_id,
            "workspace_dir": self.workspace_dir,
            "run_dir": self.run_paths.run_dir,
            "goal": goal,
            "judge_brief": judge_brief,
            "score": canonical["score"],
            "status": status,
            "success": canonical["score"] >= target_score,
            "target_score": target_score,
            "manager": self.manager.model_name,
            "specialists": [model.model_name for model in self.specialists],
            "candidate_path": self.run_paths.candidate_path,
            "verified_path": self.run_paths.verified_path,
            "judge_path": self.run_paths.judge_path,
            "research_log_path": self.run_paths.research_log_path,
            "events_path": self.run_paths.events_path,
            "started_at": self.started_at,
            "finished_at": finished_at,
        }
        self.workspace.write_text(self.run_paths.summary_path, json.dumps(summary, indent=2))
        if canonical["score"] >= target_score:
            self.workspace.write_text(self.run_paths.verified_path, canonical["content"])

        self._emit(
            phase="run.finished",
            actor="system",
            message=f"Run finished with status '{status}'.",
            score=canonical["score"],
            artifacts={"summary_path": self.run_paths.summary_path},
        )

        return {
            "success": canonical["score"] >= target_score,
            "status": status,
            "run_id": self.run_paths.run_id,
            "score": canonical["score"],
            "model": canonical.get("actor", self.manager.model_name),
            "content": canonical["content"],
            "feedback": canonical["feedback"],
            "run_dir": self.run_paths.run_dir,
            "artifacts": self.run_paths.to_dict(),
        }

    def _build_baseline_prompt(
        self,
        goal: str,
        judge_brief: str,
        operator_brief: str,
        workspace_context: str,
        data_preview: str,
        resume_material: dict[str, Any],
    ) -> str:
        round_history = self._build_round_history()
        prompt = [
            "--- HUMAN OBJECTIVE ---",
            goal,
            "",
            "--- HUMAN JUDGE BRIEF ---",
            judge_brief or "No extra judge brief.",
            "",
            "--- HUMAN OPERATOR BRIEF ---",
            operator_brief or "No extra operator brief.",
            "",
            "--- EVALUATION INPUT PREVIEW ---",
            data_preview,
            "",
            "--- WORKSPACE CONTEXT ---",
            workspace_context,
            "",
            "--- ROUND HISTORY ---",
            round_history,
            "",
            "--- RESUME SUMMARY ---",
            resume_material["summary"],
            "",
        ]
        if resume_material["best_code"]:
            prompt.extend(
                [
                    "--- LAST KNOWN BEST CODE ---",
                    resume_material["best_code"],
                    "",
                ]
            )

        prompt.append(
            "TASK: Build the first working version yourself. You own the canonical solution. Output only full code."
        )
        return "\n".join(prompt)

    def _build_specialist_prompt(
        self,
        goal: str,
        judge_brief: str,
        canonical: dict[str, Any],
        round_is_verification: bool,
        directives: list[str],
    ) -> str:
        instructions = (
            "TASK: Audit the manager-owned canonical solution and the judge. Suggest final touch-ups, edge-case fixes, and score integrity improvements. Output only full code."
            if round_is_verification
            else "TASK: Explore a distinct optimization branch. Improve the canonical solution without losing the proven logic. Output only full code."
        )
        round_history = self._build_round_history()
        parts = [
            "--- USER GOAL ---",
            goal,
            "",
            "--- JUDGE BRIEF ---",
            judge_brief or "No extra judge brief.",
            "",
            "--- ROUND HISTORY (what was tried so far) ---",
            round_history,
            "",
            "--- CURRENT CANONICAL SOLUTION ---",
            canonical["content"],
            "",
            "--- LAST CANONICAL SCORE ---",
            str(canonical["score"]),
            "",
            "--- LAST CANONICAL FEEDBACK ---",
            canonical["feedback"],
            "",
            "--- CURRENT JUDGE LOGIC ---",
            self.workspace.read_text(self.run_paths.judge_path),
            "",
        ]

        if directives:
            parts.extend(
                [
                    "--- NEW HUMAN DIRECTIVES ---",
                    "\n".join(f"- {note}" for note in directives),
                    "",
                ]
            )

        parts.append(instructions)
        return "\n".join(parts)

    def _build_manager_integration_prompt(
        self,
        goal: str,
        judge_brief: str,
        canonical: dict[str, Any],
        specialist_results: list[dict[str, Any]],
        directives: list[str],
        round_is_verification: bool,
    ) -> str:
        round_history = self._build_round_history()
        prompt = [
            "--- USER GOAL ---",
            goal,
            "",
            "--- JUDGE BRIEF ---",
            judge_brief or "No extra judge brief.",
            "",
            "--- ROUND HISTORY (what was tried so far) ---",
            round_history,
            "",
            "--- CURRENT CANONICAL SOLUTION ---",
            canonical["content"],
            "",
            "--- CURRENT CANONICAL RESULT ---",
            f"Score: {canonical['score']}\nFeedback:\n{canonical['feedback']}",
            "",
            "--- CURRENT JUDGE LOGIC ---",
            self.workspace.read_text(self.run_paths.judge_path),
            "",
        ]

        if directives:
            prompt.extend(
                [
                    "--- NEW HUMAN DIRECTIVES ---",
                    "\n".join(f"- {note}" for note in directives),
                    "",
                ]
            )

        for index, result in enumerate(specialist_results, start=1):
            prompt.extend(
                [
                    f"--- SPECIALIST {index}: {result['actor']} ---",
                    result["content"],
                    "",
                    f"Score: {result['score']}",
                    f"Feedback:\n{result['feedback']}",
                    "",
                ]
            )

        prompt.append(
            (
                "TASK: You are the lead AI. Integrate the best useful changes from both specialists into the manager-owned canonical solution. "
                "Preserve what already works. Also audit the judge if it looks misleading. Output only full code."
                if round_is_verification
                else "TASK: You are the lead AI. Review both specialist branches, implement the strongest useful changes into the canonical solution, and output only full code."
            )
        )
        return "\n".join(prompt)

    def _emit(
        self,
        phase: str,
        actor: str,
        message: str,
        score: float | None = None,
        artifacts: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
    ):
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "actor": actor,
            "message": message,
            "score": score,
            "artifacts": artifacts or {},
            "meta": meta or {},
        }

        if self.run_paths:
            try:
                with open(self.run_paths.events_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event) + "\n")
            except OSError as exc:
                logging.warning("Could not append event log: %s", exc)

        if self.event_callback:
            self.event_callback(event)

    def _consume_directives(self) -> list[str]:
        if not self.directive_reader:
            return []

        try:
            directives = [item.strip() for item in self.directive_reader() if item and item.strip()]
        except Exception as exc:
            self._emit(
                phase="directive.error",
                actor="system",
                message=f"Could not consume directives: {exc}",
            )
            return []

        if directives:
            self._emit(
                phase="directive.received",
                actor="human",
                message="New human directives queued for the next manager round.",
                meta={"directives": directives},
            )
        return directives

    @staticmethod
    def _clean_code(raw_text: str) -> str:
        cleaned = raw_text.replace("```python", "").replace("```", "").strip()
        return cleaned

    @staticmethod
    def _validate_python(code: str) -> str | None:
        try:
            ast.parse(code)
            return None
        except SyntaxError as exc:
            return str(exc)

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
