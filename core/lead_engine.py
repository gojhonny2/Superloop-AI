import ast
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from core.ai_memory import AIMemoryStore
from core.judge_bootstrapper import JudgeBootstrapper
from core.run_context import RunPaths
from core.run_memory import RunMemory
from core.workspace_analyzer import WorkspaceAnalyzer, get_context_budget
from judges.runtime_judge import RuntimeJudge
from models.base_model import BaseModel

MANAGER_ROLE_KEY = "manager"
SPECIALIST_ROLE_KEYS = ["specialist-a", "specialist-b"]

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
        self.analyzer = WorkspaceAnalyzer(self.workspace_dir)
        self.memory = RunMemory(self.workspace_dir)
        self.bootstrapper = JudgeBootstrapper(manager)
        self.judge: RuntimeJudge | None = None
        self.run_paths: RunPaths | None = None
        self.research_trail: list[dict[str, Any]] = []
        self.event_callback: Callable[[dict[str, Any]], None] | None = None
        self.directive_reader: Callable[[], list[str]] | None = None
        self.started_at: str = ""
        self.analysis_brief: str = ""
        self.ai_memory: AIMemoryStore | None = None
        self.goal_spec: str = ""

    def _specialist_role_key(self, index: int) -> str:
        if 0 <= index < len(SPECIALIST_ROLE_KEYS):
            return SPECIALIST_ROLE_KEYS[index]
        return f"specialist-{index + 1}"

    def _load_memory(self, role_key: str) -> str:
        if self.ai_memory is None:
            return "No prior notes yet."
        return self.ai_memory.load(role_key)

    def _record_memory(
        self,
        role_key: str,
        iteration: int,
        approach: str = "",
        score: float | None = None,
        judge_feedback: str = "",
    ) -> None:
        if self.ai_memory is None:
            return
        self.ai_memory.append(
            role_key=role_key,
            iteration=iteration,
            approach=approach,
            score=score,
            judge_feedback=judge_feedback,
        )

    async def _run_judge_audit(
        self,
        goal: str,
        canonical: dict[str, Any],
    ) -> dict[str, Any]:
        """Final meta-audit: did the judge itself measure the right thing?

        Returns a (possibly updated) canonical dict. If the manager found a
        real blind spot, the judge is rewritten and the canonical candidate
        is re-scored against the fixed judge exactly once.
        """
        if self.run_paths is None or self.judge is None:
            return canonical

        try:
            with open(self.run_paths.judge_path, "r", encoding="utf-8") as handle:
                current_judge = handle.read()
        except OSError:
            current_judge = ""

        self._emit(
            phase="judge.auditing",
            actor=self.manager.model_name,
            message="Manager is auditing whether the judge actually measured the real goal.",
            score=canonical.get("score"),
        )

        try:
            raw = await asyncio.wait_for(
                self._generate_with_model(
                    self.manager,
                    self._build_judge_audit_prompt(
                        goal=goal,
                        canonical=canonical,
                        judge_code=current_judge,
                    ),
                    "You are the lead engineer auditing the judge. Declare PASS unless the "
                    "judge has a real blind spot that could mask failure.",
                ),
                timeout=240.0,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            self._emit(
                phase="judge.audit_skipped",
                actor=self.manager.model_name,
                message=f"Judge audit could not be produced: {exc}",
            )
            return canonical

        verdict, reasoning, new_judge_code = self._parse_judge_audit(raw or "")

        audit_path = ""
        if self.run_paths is not None:
            audit_path = os.path.join(self.run_paths.run_dir, "judge_audit.md")
            try:
                with open(audit_path, "w", encoding="utf-8") as handle:
                    handle.write(
                        "# Judge audit\n\n"
                        f"## Verdict\n{verdict or 'UNKNOWN'}\n\n"
                        f"## Reasoning\n{reasoning or 'None.'}\n"
                    )
            except OSError:
                audit_path = ""

        if verdict != "FIX" or not new_judge_code.strip():
            self._emit(
                phase="judge.audit_passed",
                actor=self.manager.model_name,
                message="Judge audit passed - the judge is measuring the real goal.",
                artifacts={"audit_path": audit_path} if audit_path else {},
            )
            return canonical

        syntax_err = self._validate_python(new_judge_code)
        if syntax_err:
            self._emit(
                phase="judge.audit_skipped",
                actor=self.manager.model_name,
                message=f"Proposed judge fix is not valid Python: {syntax_err}",
                artifacts={"audit_path": audit_path} if audit_path else {},
            )
            return canonical

        backup_path = ""
        try:
            backup_path = os.path.join(
                self.run_paths.artifacts_dir,
                "judge_logic_pre_audit.py",
            )
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            with open(backup_path, "w", encoding="utf-8") as handle:
                handle.write(current_judge)
        except OSError:
            backup_path = ""

        try:
            with open(self.run_paths.judge_path, "w", encoding="utf-8") as handle:
                handle.write(new_judge_code)
        except OSError as exc:
            self._emit(
                phase="judge.audit_skipped",
                actor=self.manager.model_name,
                message=f"Could not write updated judge: {exc}",
            )
            return canonical

        self._emit(
            phase="judge.audit_fixed",
            actor=self.manager.model_name,
            message="Manager rewrote the judge to fix a blind spot. Re-evaluating the canonical candidate once.",
            artifacts={
                "audit_path": audit_path,
                "judge_backup_path": backup_path,
                "judge_path": self.run_paths.judge_path,
            },
        )

        reverified = self._evaluate_candidate(
            iteration=canonical.get("iteration", -1),
            actor=self.manager.model_name,
            role="manager-reverified",
            code=canonical.get("content", ""),
            artifact_name="audit_reverified_candidate.py",
        )

        direction = "raised" if reverified["score"] >= canonical["score"] else "lowered"
        self._emit(
            phase="judge.audit_reverified",
            actor=self.manager.model_name,
            message=(
                f"Fixed judge {direction} the canonical score to {reverified['score']:.2f} "
                f"(was {canonical['score']:.2f})."
            ),
            score=reverified["score"],
        )
        return reverified

    @staticmethod
    def _parse_judge_audit(raw: str) -> tuple[str, str, str]:
        """Return (verdict, reasoning, judge_code) parsed from the manager's audit."""
        if not raw:
            return "", "", ""

        def section(start_marker: str, end_marker: str | None) -> str:
            start = raw.find(start_marker)
            if start < 0:
                return ""
            start += len(start_marker)
            if end_marker is None:
                return raw[start:].strip()
            end = raw.find(end_marker, start)
            if end < 0:
                return raw[start:].strip()
            return raw[start:end].strip()

        verdict_block = section("<<JUDGE_AUDIT_VERDICT>>", "<<JUDGE_AUDIT_REASONING>>")
        reasoning = section("<<JUDGE_AUDIT_REASONING>>", "<<JUDGE_CODE_BEGIN>>")
        code_block = section("<<JUDGE_CODE_BEGIN>>", "<<JUDGE_CODE_END>>")

        verdict_upper = verdict_block.upper()
        if "FIX" in verdict_upper:
            verdict = "FIX"
        elif "PASS" in verdict_upper:
            verdict = "PASS"
        else:
            verdict = ""

        cleaned_code = code_block.replace("```python", "").replace("```", "").strip()
        return verdict, reasoning, cleaned_code

    async def _generate_specialist_briefs(
        self,
        goal: str,
        canonical: dict[str, Any],
        round_is_verification: bool,
        iteration: int,
        directives: list[str],
    ) -> list[str]:
        """Ask the manager to write one distinct brief per specialist.

        Returns a list the same length as ``self.specialists``. Entries may
        be empty strings when the manager could not produce a clean brief;
        the specialist still runs, it just falls back to the shared context.
        """
        count = len(self.specialists)
        empty = [""] * count

        self._emit(
            phase="specialists.briefing",
            actor=self.manager.model_name,
            message="Manager is drafting distinct briefs for each specialist.",
            meta={"iteration": iteration, "count": count},
        )

        try:
            raw = await asyncio.wait_for(
                self._generate_with_model(
                    self.manager,
                    self._build_specialist_briefing_prompt(
                        goal=goal,
                        canonical=canonical,
                        round_is_verification=round_is_verification,
                        iteration=iteration,
                        directives=directives,
                    ),
                    "You are the lead engineer splitting work between specialists. Produce "
                    "DISTINCT, concrete briefs - never two copies of the same idea.",
                ),
                timeout=180.0,
            )
        except (asyncio.TimeoutError, Exception):
            raw = ""

        briefs = self._parse_specialist_briefs(raw or "", count)

        briefs_path = ""
        if self.run_paths is not None:
            briefs_path = os.path.join(
                self.run_paths.artifacts_dir,
                f"round_{iteration:02d}_specialist_briefs.md",
            )
            try:
                os.makedirs(os.path.dirname(briefs_path), exist_ok=True)
                blocks = []
                for i, text in enumerate(briefs):
                    label = self._specialist_role_key(i)
                    blocks.append(
                        f"## Brief for {label}\n\n{text.strip() or 'No brief produced.'}\n"
                    )
                with open(briefs_path, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(blocks))
            except OSError:
                briefs_path = ""

        produced = sum(1 for b in briefs if b.strip())
        if produced:
            self._emit(
                phase="specialists.briefed",
                actor=self.manager.model_name,
                message=f"Manager wrote {produced} distinct brief(s) for the specialists.",
                artifacts={"briefs_path": briefs_path} if briefs_path else {},
                meta={"iteration": iteration},
            )
        else:
            self._emit(
                phase="specialists.briefed",
                actor=self.manager.model_name,
                message="Manager could not draft per-specialist briefs; specialists will use the shared context.",
                meta={"iteration": iteration},
            )

        return briefs or empty

    @staticmethod
    def _parse_specialist_briefs(raw: str, count: int) -> list[str]:
        """Split the manager's structured output into per-specialist briefs."""
        if count <= 0:
            return []
        briefs = [""] * count
        if not raw:
            return briefs
        markers = [
            f"<<BRIEF_FOR_SPECIALIST_{chr(ord('A') + i)}>>"
            for i in range(count)
        ]
        positions = [raw.find(marker) for marker in markers]
        for i, marker in enumerate(markers):
            start = positions[i]
            if start < 0:
                continue
            start += len(marker)
            end = len(raw)
            for j, other_idx in enumerate(positions):
                if j != i and other_idx > start and other_idx < end:
                    end = other_idx
            briefs[i] = raw[start:end].strip()
        return briefs

    def _build_round_history(self) -> str:
        """Summary of completed rounds for the debate memory.

        Kept bounded so context does not balloon after many iterations:
        we always show the best-ever round (by canonical score) plus the
        three most recent rounds. Older middle rounds are collapsed into
        a single one-line marker.
        """
        if not self.research_trail:
            return "No previous rounds yet."

        def canon(entry: dict[str, Any]) -> dict[str, Any]:
            return entry.get("canonical_after") or entry.get("canonical", {})

        recent = self.research_trail[-3:]
        best = max(self.research_trail, key=lambda e: canon(e).get("score", 0.0))
        selected: list[dict[str, Any]] = []
        if best not in recent:
            selected.append(best)
        selected.extend(recent)

        shown_iters = {e.get("iteration") for e in selected}
        collapsed = [e for e in self.research_trail if e.get("iteration") not in shown_iters]

        lines: list[str] = []
        if collapsed:
            scores = [canon(e).get("score", 0.0) for e in collapsed]
            lines.append(
                f"({len(collapsed)} earlier round(s) collapsed — scores ranged "
                f"{min(scores):.2f}–{max(scores):.2f}.)"
            )
            lines.append("")

        for entry in selected:
            iteration = entry.get("iteration", "?")
            mode = entry.get("mode", "unknown")
            canonical_after = canon(entry)
            score = canonical_after.get("score", 0.0)
            feedback = (canonical_after.get("feedback") or "")[:280]

            tag = " [BEST SO FAR]" if entry is best else ""
            lines.append(f"Round {iteration} ({mode}){tag}:")
            lines.append(f"  Canonical score after round: {score:.2f}")

            specialist_summaries = []
            for spec in entry.get("specialists", []):
                specialist_summaries.append(
                    f"  - {spec.get('actor', '?')}: score {spec.get('score', 0.0):.2f}"
                )
            if specialist_summaries:
                lines.append("  Specialist scores:")
                lines.extend(specialist_summaries)
            lines.append(f"  Judge feedback summary: {feedback}")
            lines.append("")

        return "\n".join(lines)

    def _build_goal_expansion_prompt(
        self,
        goal: str,
        operator_brief: str,
        workspace_context: str,
    ) -> str:
        return (
            "You are the lead engineer on Superloop. Before any code is written, turn the short "
            "user goal below into a concrete, workspace-aware specification that every downstream "
            "step (judge, baseline, specialists, integration) will read.\n\n"
            f"--- SHORT USER GOAL (from the operator) ---\n{goal}\n\n"
            f"--- HUMAN OPERATOR BRIEF ---\n{operator_brief or 'No extra operator brief.'}\n\n"
            f"--- COMPLETE WORKSPACE (every file, every line) ---\n{workspace_context}\n\n"
            "Write the specification with EXACTLY these sections and nothing else. Stay grounded in "
            "what the workspace makes possible — do not invent tech that is not already present:\n"
            "1. ONE_LINE_PURPOSE: a single sentence restating the goal in concrete terms.\n"
            "2. WORKING_DEFINITION_OF_DONE: bullet list of observable outcomes that mean the goal is met.\n"
            "3. FEATURES_OR_BEHAVIOURS: bullet list of specific capabilities the candidate must have, "
            "phrased as user-visible behaviour (not implementation detail).\n"
            "4. WORKSPACE_TOUCHPOINTS: bullet list of the exact files, modules, or data assets the "
            "candidate must interact with, using real relative paths from the workspace.\n"
            "5. OUT_OF_SCOPE: bullet list of things NOT to build (to keep the change focused).\n\n"
            "Keep the whole spec under 400 words. Plain text bullets. No code blocks."
        )

    def _build_specialist_briefing_prompt(
        self,
        goal: str,
        canonical: dict[str, Any],
        round_is_verification: bool,
        iteration: int,
        directives: list[str],
    ) -> str:
        round_history = self._build_round_history()
        mode = "verification and polish" if round_is_verification else "optimization"
        specialist_count = len(self.specialists)
        marker_list = ", ".join(
            f"<<BRIEF_FOR_SPECIALIST_{chr(ord('A') + i)}>>"
            for i in range(specialist_count)
        )
        directive_section = (
            "\n".join(f"- {note}" for note in directives) if directives else "None."
        )
        format_lines = []
        for i in range(specialist_count):
            letter = chr(ord("A") + i)
            format_lines.append(f"<<BRIEF_FOR_SPECIALIST_{letter}>>")
            format_lines.append(
                "<3-6 bullet points telling this specialist exactly what angle to attack, "
                "which files or behaviours to change, and what to avoid. "
                "Point at a specific weak spot from the judge feedback.>"
            )
        format_block = "\n".join(format_lines)
        return (
            "You are the lead engineer on Superloop. Your specialists are about to each take "
            "a swing at improving the current canonical solution. Before they start, give "
            "each one a DISTINCT, focused brief so they do not duplicate work and they each "
            "attack a real weak spot the judge called out.\n\n"
            f"--- CURRENT ROUND ---\nRound {iteration} - {mode} mode.\n\n"
            f"--- USER GOAL ---\n{goal}\n\n"
            "--- EXPANDED GOAL SPEC ---\n"
            f"{self.goal_spec or 'No expanded spec available.'}\n\n"
            "--- JUDGE EVALUATION PLAN ---\n"
            f"{self.analysis_brief or 'No analysis plan available.'}\n\n"
            f"--- CURRENT CANONICAL SCORE ---\n{canonical.get('score', 0.0)}\n\n"
            "--- CURRENT JUDGE FEEDBACK (what is still weak) ---\n"
            f"{canonical.get('feedback', '') or 'No feedback yet.'}\n\n"
            "--- ROUND HISTORY (what has already been tried) ---\n"
            f"{round_history}\n\n"
            f"--- NEW HUMAN DIRECTIVES ---\n{directive_section}\n\n"
            f"Write {specialist_count} briefs using EXACTLY this format (these markers: "
            f"{marker_list}):\n"
            f"{format_block}\n\n"
            "Rules:\n"
            "- Each brief must target a DIFFERENT weak spot or strategy. Never duplicate.\n"
            "- Be concrete: name real files, real behaviours, real metrics from the judge.\n"
            "- No code blocks. Plain text bullets only.\n"
            "- Under 200 words total across all briefs."
        )

    def _build_judge_audit_prompt(
        self,
        goal: str,
        canonical: dict[str, Any],
        judge_code: str,
    ) -> str:
        return (
            "You are the lead engineer doing a FINAL META-AUDIT of the scoring judge itself "
            "before declaring the run complete. The loop hit the target score, but that score "
            "only matters if the judge was actually measuring the right thing. Guard against:\n"
            "- Judge-hacking: the candidate gamed a narrow metric without meeting the real goal.\n"
            "- Blind spots: the judge ignores something critical the goal implies.\n"
            "- Wrong metric: the judge measures adjacent behaviour, not the goal behaviour.\n\n"
            f"--- USER GOAL ---\n{goal}\n\n"
            "--- EXPANDED GOAL SPEC ---\n"
            f"{self.goal_spec or 'No expanded spec available.'}\n\n"
            "--- JUDGE EVALUATION PLAN ---\n"
            f"{self.analysis_brief or 'No analysis plan available.'}\n\n"
            f"--- FINAL CANONICAL SCORE ---\n{canonical.get('score', 0.0)}\n\n"
            "--- FINAL CANONICAL FEEDBACK ---\n"
            f"{canonical.get('feedback', '') or 'No feedback.'}\n\n"
            "--- CURRENT JUDGE CODE (judge_logic.py) ---\n"
            f"{judge_code}\n\n"
            "--- CURRENT CANDIDATE CODE (candidate_solution.py) ---\n"
            f"{canonical.get('content', '')}\n\n"
            "Produce a report using EXACTLY this format (all markers required):\n"
            "<<JUDGE_AUDIT_VERDICT>>\n"
            "PASS  or  FIX\n"
            "<<JUDGE_AUDIT_REASONING>>\n"
            "<2-5 bullet points explaining whether the judge is measuring the real goal.>\n"
            "<<JUDGE_CODE_BEGIN>>\n"
            "<If verdict is FIX: the FULL replacement judge_logic.py here. "
            "If verdict is PASS: leave this block empty.>\n"
            "<<JUDGE_CODE_END>>\n\n"
            "Rules:\n"
            "- Only emit FIX when the judge has a real blind spot that could mask failure.\n"
            "- FIX output: the new judge MUST still print 'FINAL_SCORE: <float>', still work with "
            "candidate_solution.py, and still respect the SUPERLOOP_WORKSPACE_DIR env var for "
            "any workspace paths.\n"
            "- Do not change the judge for taste. Only fix real issues."
        )

    def _build_analysis_plan_prompt(
        self,
        goal: str,
        judge_brief: str,
        workspace_context: str,
        judge_code: str,
    ) -> str:
        return (
            "You are the judge and planner for Superloop. The manager model (you) has just written "
            "the scoring script below. BEFORE any candidate code is written, analyse the goal against "
            "the workspace and produce a short, structured evaluation plan. Every downstream step "
            "(baseline, specialists, integration) will receive your plan verbatim, so be concrete.\n\n"
            f"--- USER GOAL ---\n{goal}\n\n"
            f"--- JUDGE BRIEF ---\n{judge_brief or 'No extra judge brief.'}\n\n"
            f"--- SCORING SCRIPT YOU JUST WROTE ---\n{judge_code}\n\n"
            f"--- COMPLETE WORKSPACE (every file the scoring script can reach) ---\n{workspace_context}\n\n"
            "Write a plan with EXACTLY these sections and nothing else:\n"
            "1. WHAT_THE_JUDGE_MEASURES: bullet list of the concrete metrics/signals the scoring script "
            "computes. Describe what each metric means for THIS project, not generically.\n"
            "2. KEY_FILES_AND_DATA: bullet list of the real workspace files (code modules, datasets, "
            "config) a candidate MUST use or respect. Use exact relative paths.\n"
            "3. WHAT_A_WINNING_CANDIDATE_LOOKS_LIKE: bullet list of observable properties the winning "
            "candidate must have.\n"
            "4. COMMON_FAILURE_MODES: bullet list of mistakes likely to score 0.0 (wrong paths, broken "
            "imports, mocked data, missing metric, etc.).\n"
            "5. GREEN_LIGHT_CHECK: one short sentence stating the minimum-bar every candidate should "
            "clear before the judge even runs.\n\n"
            "Keep the whole plan under 400 words. No code blocks. Plain text bullets."
        )

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
        self.ai_memory = AIMemoryStore(self.run_paths.run_dir)
        self.goal_spec = ""
        self.analysis_brief = ""
        resume_material = self.memory.get_resume_material(resume=resume)

        self._emit(
            phase="run.started",
            actor="system",
            message="Superloop run created.",
            meta={"run_id": self.run_paths.run_id, "target_score": target_score},
        )

        try:
            # ── Step 1: Deep workspace analysis ──
            self._emit(
                phase="workspace.analyzing",
                actor="system",
                message=f"Reading every file in {self.workspace_dir}...",
            )
            analysis = await asyncio.to_thread(self.analyzer.analyze)
            self._emit(
                phase="workspace.analyzed",
                actor="system",
                message=f"Analyzed {analysis['total_files']} files across {analysis['total_dirs']} directories. "
                        f"Project type: {analysis['project_type']}. "
                        f"Languages: {', '.join(analysis['languages'].keys())}.",
                meta={
                    "total_files": analysis["total_files"],
                    "code_files": analysis["code_files"],
                    "project_type": analysis["project_type"],
                    "entry_points": analysis["entry_points"],
                },
            )

            # ── Step 2: Build context sized for each model ──
            self._emit(
                phase="workspace.context_building",
                actor="system",
                message="Preparing model context from the analyzed workspace.",
            )
            manager_context, judge_context = await asyncio.gather(
                asyncio.to_thread(self.analyzer.build_full_context, self.manager.model_name),
                asyncio.to_thread(self.analyzer.build_judge_context, self.manager.model_name),
            )
            judge_structure, judge_files = judge_context
            self._emit(
                phase="workspace.context_ready",
                actor="system",
                message="Workspace context prepared for the manager and judge.",
                meta={
                    "manager_context_chars": len(manager_context),
                    "judge_context_chars": len(judge_files),
                },
            )

            data_preview = ""
            if data_path:
                try:
                    with open(data_path, "r", encoding="utf-8") as f:
                        data_preview = "".join(f.readlines()[:12]).strip()
                except (OSError, UnicodeDecodeError):
                    data_preview = "FAILED TO READ DATA FILE."

            # ── Step 2.5: Expand the short user goal into a workspace-aware spec.
            # Every downstream step (judge, baseline, specialists, integration)
            # reads this expanded spec so the user can type less and still get
            # focused work.
            self._emit(
                phase="goal.expanding",
                actor=self.manager.model_name,
                message="Manager is expanding the goal into a workspace-aware spec.",
            )
            try:
                goal_spec_text = await asyncio.wait_for(
                    self._generate_with_model(
                        self.manager,
                        self._build_goal_expansion_prompt(
                            goal=goal,
                            operator_brief=operator_brief,
                            workspace_context=manager_context,
                        ),
                        "You are the lead engineer turning a short goal into a clear, concrete "
                        "specification for the rest of the team. Stay grounded in the workspace.",
                    ),
                    timeout=240.0,
                )
            except asyncio.TimeoutError:
                goal_spec_text = ""
            self.goal_spec = (goal_spec_text or "").strip()
            if self.goal_spec:
                spec_path = os.path.join(self.run_paths.run_dir, "goal_spec.md")
                try:
                    with open(spec_path, "w", encoding="utf-8") as handle:
                        handle.write(self.goal_spec)
                except OSError:
                    spec_path = ""
                self._emit(
                    phase="goal.ready",
                    actor=self.manager.model_name,
                    message="Goal spec drafted and shared with every downstream step.",
                    artifacts={"goal_spec_path": spec_path} if spec_path else {},
                )
            else:
                self._emit(
                    phase="goal.unchanged",
                    actor=self.manager.model_name,
                    message="Manager did not return an expanded goal spec; using the raw goal.",
                )

            # ── Step 3: Bootstrap judge WITH full workspace knowledge ──
            self._emit(
                phase="judge.generating",
                actor=self.manager.model_name,
                message="Manager is generating the judge logic.",
            )
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        self.bootstrapper.bootstrap_judge,
                        data_path,
                        goal,
                        judge_brief,
                        self.run_paths.judge_path,
                        "candidate_solution.py",
                        judge_files,
                        judge_structure,
                    ),
                    timeout=900.0,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"Provider did not return judge logic from '{self.manager.model_name}' "
                    "within 900s even with retries. This is usually a slow provider or a "
                    "very large workspace - retrying often works. You can also try a smaller "
                    "workspace or a faster provider key."
                ) from exc
            self._emit(
                phase="judge.ready",
                actor=self.manager.model_name,
                message="Manager created the judge logic with full workspace awareness.",
                artifacts={"judge_path": self.run_paths.judge_path},
            )

            self.judge = RuntimeJudge(
                run_dir=self.run_paths.run_dir,
                command=["python", "judge_logic.py"],
                target_score_regex=r"FINAL_SCORE:\s*(\d+\.?\d*)",
                generated_filename=os.path.basename(self.run_paths.candidate_path),
                cleanup_generated=False,
                workspace_dir=self.workspace_dir,
            )

            # ── Step 3.5: Pre-baseline analysis — the judge+manager studies the
            # workspace and outputs a structured evaluation plan that is fed into
            # every downstream prompt (baseline, specialists, integration).
            try:
                with open(self.run_paths.judge_path, "r", encoding="utf-8") as f:
                    judge_code_for_plan = f.read()
            except OSError:
                judge_code_for_plan = ""

            self._emit(
                phase="plan.analyzing",
                actor=self.manager.model_name,
                message="Manager is studying the workspace and drafting the evaluation plan.",
            )
            try:
                analysis_plan = await asyncio.wait_for(
                    self._generate_with_model(
                        self.manager,
                        self._build_analysis_plan_prompt(
                            goal=goal,
                            judge_brief=judge_brief,
                            workspace_context=manager_context,
                            judge_code=judge_code_for_plan,
                        ),
                        "You are the lead engineer analysing what a candidate must do to win. "
                        "Produce a concrete, workspace-specific plan — never generic advice.",
                    ),
                    timeout=240.0,
                )
            except asyncio.TimeoutError:
                analysis_plan = ""
            self.analysis_brief = (analysis_plan or "").strip()
            if self.analysis_brief:
                plan_path = os.path.join(self.run_paths.run_dir, "analysis_plan.md")
                try:
                    with open(plan_path, "w", encoding="utf-8") as handle:
                        handle.write(self.analysis_brief)
                except OSError:
                    plan_path = ""
                self._emit(
                    phase="plan.ready",
                    actor=self.manager.model_name,
                    message="Evaluation plan drafted and shared with every downstream step.",
                    artifacts={"plan_path": plan_path} if plan_path else {},
                )
            else:
                self._emit(
                    phase="plan.skipped",
                    actor=self.manager.model_name,
                    message="Manager did not return an analysis plan; continuing without it.",
                )

            # ── Step 4: Manager baseline ──
            baseline_prompt = self._build_baseline_prompt(
                goal=goal,
                judge_brief=judge_brief,
                operator_brief=operator_brief,
                workspace_context=manager_context,
                data_preview=data_preview,
                resume_material=resume_material,
            )
            baseline_code = self._clean_code(await self._generate_with_model(
                self.manager,
                baseline_prompt,
                "You are the lead AI engineer. You have been given the COMPLETE workspace — every file, every line. "
                "Your job is to MODIFY or EXTEND the existing project to achieve the goal. "
                "Do NOT rewrite from scratch. Output only the full code for the candidate file.",
            ))
            canonical = self._evaluate_candidate(
                iteration=0,
                actor=self.manager.model_name,
                role="manager-baseline",
                code=baseline_code,
                artifact_name="round_00_manager_baseline.py",
            )
            self._record_memory(
                MANAGER_ROLE_KEY,
                iteration=0,
                approach="baseline candidate",
                score=canonical["score"],
                judge_feedback=canonical["feedback"],
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

            # ── Step 5: Optimization loop ──
            while iteration <= max_iterations or verification_mode:
                round_is_verification = verification_mode
                directives = self._consume_directives()

                # Build specialist context sized for each specialist model so
                # each one sees the workspace at the size its model can handle.
                specialist_contexts = []
                for model in self.specialists:
                    specialist_contexts.append(self.analyzer.build_full_context(model.model_name))

                specialist_briefs = await self._generate_specialist_briefs(
                    goal=goal,
                    canonical=canonical,
                    round_is_verification=round_is_verification,
                    iteration=iteration,
                    directives=directives,
                )

                specialist_prompts = []
                for index, model in enumerate(self.specialists):
                    role_key = self._specialist_role_key(index)
                    brief = specialist_briefs[index] if index < len(specialist_briefs) else ""
                    specialist_prompts.append(
                        self._build_specialist_prompt(
                            goal=goal,
                            judge_brief=judge_brief,
                            canonical=canonical,
                            round_is_verification=round_is_verification,
                            directives=directives,
                            role_key=role_key,
                            workspace_context=specialist_contexts[index],
                            specialist_brief=brief,
                        )
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

                specialist_system_prompt = (
                    "You are auditing the manager's result. You have seen the FULL workspace. "
                    "The candidate must work with the EXISTING project. Output only improved full code."
                    if round_is_verification
                    else "You are an optimization specialist. You have seen the FULL workspace. "
                    "Improve the code while keeping it compatible with the existing project. "
                    "Output only full code."
                )
                specialist_payloads = await asyncio.gather(
                    *[
                        self._generate_with_model(model, specialist_prompts[i], specialist_system_prompt)
                        for i, model in enumerate(self.specialists)
                    ],
                    return_exceptions=True,
                )

                specialist_results: list[dict[str, Any]] = []
                for index, (model, proposal) in enumerate(zip(self.specialists, specialist_payloads)):
                    role_key = self._specialist_role_key(index)
                    failure_reason: str | None = None
                    if isinstance(proposal, Exception):
                        failure_reason = f"Specialist model failed: {proposal}"
                    elif proposal is None or not str(proposal).strip():
                        failure_reason = (
                            "Specialist returned empty content (provider likely rate-limited "
                            "or dropped the response). Skipping this branch."
                        )
                    if failure_reason:
                        self._emit(
                            phase="specialist.failed",
                            actor=model.model_name,
                            message=failure_reason,
                            meta={"iteration": iteration, "role": "specialist-branch"},
                        )
                        specialist_results.append({
                            "iteration": iteration,
                            "actor": model.model_name,
                            "role": "specialist-verification" if round_is_verification else "specialist-branch",
                            "content": canonical["content"],
                            "score": 0.0,
                            "success": False,
                            "feedback": failure_reason,
                            "artifact_path": "",
                        })
                        self._record_memory(
                            role_key,
                            iteration=iteration,
                            approach="attempt skipped",
                            score=0.0,
                            judge_feedback=failure_reason,
                        )
                        continue
                    spec_result = self._evaluate_candidate(
                        iteration=iteration,
                        actor=model.model_name,
                        role="specialist-verification" if round_is_verification else "specialist-branch",
                        code=self._clean_code(proposal),
                        artifact_name=f"round_{iteration:02d}_{self._slug(model.model_name)}.py",
                    )
                    specialist_results.append(spec_result)
                    self._record_memory(
                        role_key,
                        iteration=iteration,
                        approach="verification polish" if round_is_verification else "optimization branch",
                        score=spec_result["score"],
                        judge_feedback=spec_result["feedback"],
                    )

                integration_prompt = self._build_manager_integration_prompt(
                    goal=goal,
                    judge_brief=judge_brief,
                    canonical=canonical,
                    specialist_results=specialist_results,
                    directives=directives,
                    round_is_verification=round_is_verification,
                )
                manager_candidate_code = self._clean_code(await self._generate_with_model(
                    self.manager,
                    integration_prompt,
                    "You are the lead AI engineer. Review both specialist branches, integrate the useful changes "
                    "into the canonical solution. The code must work with the existing workspace project. "
                    "Output only the full final code.",
                ))
                manager_result = self._evaluate_candidate(
                    iteration=iteration,
                    actor=self.manager.model_name,
                    role="manager-verification" if round_is_verification else "manager-integration",
                    code=manager_candidate_code,
                    artifact_name=f"round_{iteration:02d}_manager_integrated.py",
                )
                self._record_memory(
                    MANAGER_ROLE_KEY,
                    iteration=iteration,
                    approach=(
                        "verification polish of canonical"
                        if round_is_verification
                        else "integrated both specialist branches"
                    ),
                    score=manager_result["score"],
                    judge_feedback=manager_result["feedback"],
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
                    audited = await self._run_judge_audit(goal=goal, canonical=canonical)
                    if audited is not canonical:
                        self._persist_canonical(audited)
                    return self._finalize_run(
                        goal=goal,
                        target_score=target_score,
                        judge_brief=judge_brief,
                        canonical=audited,
                        status="completed" if audited["score"] >= target_score else "completed_with_warnings",
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

        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(code)

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
        os.makedirs(os.path.dirname(self.run_paths.candidate_path), exist_ok=True)
        with open(self.run_paths.candidate_path, "w", encoding="utf-8") as f:
            f.write(canonical["content"])

    def _record_round(self, round_summary: dict[str, Any]):
        self.research_trail.append(round_summary)
        self._export_research_log()

    def _export_research_log(self):
        lines = [json.dumps(entry) for entry in self.research_trail]
        os.makedirs(os.path.dirname(self.run_paths.research_log_path), exist_ok=True)
        with open(self.run_paths.research_log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))

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

        os.makedirs(os.path.dirname(self.run_paths.summary_path), exist_ok=True)
        with open(self.run_paths.summary_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(summary, indent=2))

        if canonical["score"] >= target_score:
            with open(self.run_paths.verified_path, "w", encoding="utf-8") as f:
                f.write(canonical["content"])

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
        my_memory = self._load_memory(MANAGER_ROLE_KEY)
        prompt = [
            "--- HUMAN OBJECTIVE ---",
            goal,
            "",
            "--- EXPANDED GOAL SPEC (workspace-aware interpretation) ---",
            self.goal_spec or "No expanded spec available; use the raw human objective above.",
            "",
            "--- HUMAN JUDGE BRIEF ---",
            judge_brief or "No extra judge brief.",
            "",
            "--- HUMAN OPERATOR BRIEF ---",
            operator_brief or "No extra operator brief.",
            "",
            "--- JUDGE EVALUATION PLAN (read before writing any code) ---",
            self.analysis_brief or "No analysis plan available.",
            "",
            "--- MY MEMORY (notes from my prior rounds in this run) ---",
            my_memory,
            "",
            "--- EVALUATION INPUT PREVIEW ---",
            data_preview or "No external data file provided.",
            "",
            "--- COMPLETE WORKSPACE (every file, every line) ---",
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
            "TASK: You have been given the COMPLETE workspace above — every file, every line of code. "
            "The project already exists and works. Your job is to achieve the human objective by MODIFYING "
            "or EXTENDING the existing code. Do NOT rewrite the project from scratch. "
            "Output only the full code for the candidate file."
        )
        return "\n".join(prompt)

    def _build_specialist_prompt(
        self,
        goal: str,
        judge_brief: str,
        canonical: dict[str, Any],
        round_is_verification: bool,
        directives: list[str],
        role_key: str = "",
        workspace_context: str = "",
        specialist_brief: str = "",
    ) -> str:
        instructions = (
            "TASK: Audit the canonical solution and the judge. The solution must work with the existing project. "
            "Suggest final touch-ups, edge-case fixes, and score integrity improvements. Output only full code."
            if round_is_verification
            else "TASK: Explore a distinct optimization branch. The solution must work with the existing project. "
            "Improve the canonical solution without losing the proven logic. Output only full code."
        )
        round_history = self._build_round_history()
        judge_code = ""
        try:
            with open(self.run_paths.judge_path, "r", encoding="utf-8") as f:
                judge_code = f.read()
        except OSError:
            pass

        my_memory = self._load_memory(role_key) if role_key else "No prior notes yet."
        brief_text = (specialist_brief or "").strip()

        parts = [
            "--- USER GOAL ---",
            goal,
            "",
            "--- EXPANDED GOAL SPEC (workspace-aware interpretation) ---",
            self.goal_spec or "No expanded spec available; use the raw user goal above.",
            "",
            "--- YOUR SPECIFIC BRIEF FOR THIS ROUND (from the manager) ---",
            brief_text or "No specific brief; improve the canonical solution using the shared context.",
            "",
            "--- JUDGE BRIEF ---",
            judge_brief or "No extra judge brief.",
            "",
            "--- JUDGE EVALUATION PLAN ---",
            self.analysis_brief or "No analysis plan available.",
            "",
            "--- MY MEMORY (notes from my prior rounds in this run) ---",
            my_memory,
            "",
            "--- ROUND HISTORY (what was tried so far) ---",
            round_history,
            "",
        ]

        if workspace_context:
            parts.extend([
                "--- COMPLETE WORKSPACE (every file, every line) ---",
                workspace_context,
                "",
            ])

        parts.extend([
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
            judge_code,
            "",
        ])

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
        judge_code = ""
        try:
            with open(self.run_paths.judge_path, "r", encoding="utf-8") as f:
                judge_code = f.read()
        except OSError:
            pass

        my_memory = self._load_memory(MANAGER_ROLE_KEY)
        prompt = [
            "--- USER GOAL ---",
            goal,
            "",
            "--- EXPANDED GOAL SPEC (workspace-aware interpretation) ---",
            self.goal_spec or "No expanded spec available; use the raw user goal above.",
            "",
            "--- JUDGE BRIEF ---",
            judge_brief or "No extra judge brief.",
            "",
            "--- JUDGE EVALUATION PLAN ---",
            self.analysis_brief or "No analysis plan available.",
            "",
            "--- MY MEMORY (notes from my prior rounds in this run) ---",
            my_memory,
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
            judge_code,
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
            "TASK: You are the lead AI. Integrate the best useful changes from both specialists into the canonical solution. "
            "The code must work with the existing workspace project. "
            "Preserve what already works. Also audit the judge if it looks misleading. Output only full code."
            if round_is_verification
            else "TASK: You are the lead AI. Review both specialist branches, implement the strongest useful changes "
            "into the canonical solution. The code must work with the existing project. Output only full code."
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
