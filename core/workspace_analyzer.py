"""
Deep workspace analyzer.

Walks the entire project — every folder, every file, every line — and builds
a comprehensive profile that the manager, judge, and specialists all share.
The analysis is compressed into tiered summaries that fit different model
context windows.
"""

import os
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def _fmt_mtime(mtime: float) -> str:
    if not mtime:
        return "unknown"
    try:
        return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, OverflowError, ValueError):
        return "unknown"


CHARS_PER_TOKEN = 4

# Known context windows (tokens) for popular models
MODEL_CONTEXT_WINDOWS = {
    # OpenAI
    "gpt-4o": 128_000, "gpt-4o-mini": 128_000, "gpt-4-turbo": 128_000,
    "gpt-4": 8_192, "gpt-3.5-turbo": 16_385,
    "o1": 200_000, "o1-mini": 128_000, "o3": 200_000, "o3-mini": 200_000, "o4-mini": 200_000,
    # Anthropic
    "claude-opus-4-20250514": 200_000, "claude-sonnet-4-20250514": 200_000,
    "claude-3-7-sonnet-20250219": 200_000, "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-sonnet-20240620": 200_000, "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000, "claude-3-haiku-20240307": 200_000,
    # Google
    "gemini-2.5-pro-preview-05-06": 1_048_576, "gemini-2.5-flash-preview-04-17": 1_048_576,
    "gemini-2.0-flash": 1_048_576, "gemini-1.5-pro": 2_097_152, "gemini-1.5-flash": 1_048_576,
    # DeepSeek
    "deepseek-chat": 64_000, "deepseek-coder": 64_000, "deepseek-reasoner": 64_000,
    # Mistral
    "mistral-large-latest": 128_000, "mistral-medium-latest": 32_000,
}

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".html", ".css", ".json", ".toml", ".yaml", ".yml",
    ".md", ".txt", ".csv", ".sql", ".sh", ".bat",
    ".cfg", ".ini",
}

SKIP_DIRS = {
    ".git", ".idea", ".vscode", ".venv", "venv", "env",
    "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache",
    "catboost_info", "runs", "dist", "build", ".egg-info",
}

PRIORITY_FILENAMES = {
    "main.py", "app.py", "run.py", "run_agent.py", "agent.py", "index.py",
    "server.py", "manage.py", "cli.py", "setup.py",
    "config.py", "settings.py", "constants.py",
    "requirements.txt", "pyproject.toml", "package.json", "pipfile",
    "dockerfile", "docker-compose.yml", "makefile",
    "readme.md", "claude.md",
    "index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts",
}

LANGUAGE_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript (React)", ".jsx": "JavaScript (React)",
    ".html": "HTML", ".css": "CSS", ".json": "JSON",
    ".toml": "TOML", ".yaml": "YAML", ".yml": "YAML",
    ".sql": "SQL", ".sh": "Shell", ".bat": "Batch", ".md": "Markdown",
}


def is_windows_reserved_name(name: str) -> bool:
    """
    Detect Windows device names precisely without rejecting normal files such
    as compare.py or lptools.py.
    """
    stem = os.path.splitext(name)[0].rstrip(" .").lower()
    if stem in {"con", "prn", "aux", "nul"}:
        return True
    if len(stem) == 4 and stem[:3] in {"com", "lpt"} and stem[3].isdigit():
        return stem[3] != "0"
    return False


def safe_relpath(path: str, start: str) -> str | None:
    """Return a normalized relative path, or None for invalid Windows mounts."""
    try:
        return os.path.relpath(path, start).replace("\\", "/")
    except ValueError:
        logging.warning("Skipping path outside workspace mount: %s", path)
        return None


def get_context_budget(model_name: str) -> int:
    """Get usable character budget for a model. Reserves 40% for response + system."""
    ctx = MODEL_CONTEXT_WINDOWS.get(model_name)
    if ctx is None:
        bare = model_name.split("/")[-1] if "/" in model_name else model_name
        ctx = MODEL_CONTEXT_WINDOWS.get(bare)
    if ctx is None:
        lower = model_name.lower()
        for key, val in MODEL_CONTEXT_WINDOWS.items():
            if key in lower or lower in key:
                ctx = val
                break
    if ctx is None:
        ctx = 128_000
    return int(ctx * 0.6) * CHARS_PER_TOKEN


class WorkspaceAnalyzer:
    """
    Reads every file in the workspace and builds a full project profile.
    """

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.files: list[dict[str, Any]] = []
        self.analysis: dict[str, Any] = {}

    def analyze(self) -> dict[str, Any]:
        """Walk EVERY file and folder. Read EVERY code file line by line."""
        self.files = []
        all_dirs: list[str] = []
        language_counter: Counter = Counter()
        total_size = 0

        for root, dirs, filenames in os.walk(self.workspace_dir):
            dirs[:] = [d for d in dirs
                       if d not in SKIP_DIRS
                       and not is_windows_reserved_name(d)
                       and not d.startswith(".")
                       and not d.startswith("pytest-cache-files")]

            rel_root = safe_relpath(root, self.workspace_dir)
            if rel_root and rel_root != ".":
                all_dirs.append(rel_root)

            for filename in sorted(filenames):
                if is_windows_reserved_name(filename):
                    continue

                path = os.path.join(root, filename)
                rel_path = safe_relpath(path, self.workspace_dir)
                if not rel_path:
                    continue
                ext = os.path.splitext(filename)[1].lower()
                lower_name = filename.lower()

                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0

                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0

                total_size += size
                is_code = ext in SUPPORTED_EXTENSIONS
                content = None

                # Read every code file, every line
                if is_code and size < 200_000:
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            content = f.read()
                    except (UnicodeDecodeError, OSError):
                        content = None

                if ext in LANGUAGE_MAP:
                    language_counter[LANGUAGE_MAP[ext]] += 1

                self.files.append({
                    "path": rel_path,
                    "name": filename,
                    "ext": ext,
                    "size": size,
                    "mtime": mtime,
                    "is_code": is_code,
                    "language": LANGUAGE_MAP.get(ext, ""),
                    "is_priority": lower_name in PRIORITY_FILENAMES,
                    "depth": rel_path.count("/"),
                    "content": content,
                })

        # Sort chronologically: newest files first so the judge and every AI
        # reads the most recent intent at the top and older context after.
        self.files.sort(key=lambda f: (-f.get("mtime", 0.0), f["path"]))

        self.analysis = {
            "workspace_dir": self.workspace_dir,
            "total_files": len(self.files),
            "code_files": sum(1 for f in self.files if f["is_code"]),
            "total_size": total_size,
            "total_dirs": len(all_dirs),
            "languages": dict(language_counter.most_common(10)),
            "primary_language": language_counter.most_common(1)[0][0] if language_counter else "Unknown",
            "project_type": self._detect_project_type(),
            "entry_points": [f["path"] for f in self.files if f["is_priority"]],
            "data_files": [f["path"] for f in self.files
                           if f["ext"] in {".csv", ".json", ".parquet", ".xlsx", ".sqlite", ".db"}
                           or "data" in f["name"].lower()][:15],
            "test_files": [f["path"] for f in self.files
                           if f["name"].startswith("test_") or f["name"].endswith("_test.py")
                           or "/tests/" in f["path"] or "/test/" in f["path"]][:15],
            "config_files": [f["path"] for f in self.files
                             if f["ext"] in {".cfg", ".ini", ".toml", ".yaml", ".yml"}
                             or "config" in f["name"].lower() or "settings" in f["name"].lower()][:15],
            "dependencies": self._extract_dependencies(),
            "directory_tree": self._build_tree(),
            "suggested_goals": self._suggest_goals(),
        }
        return self.analysis

    # ── Prompt builders ──

    def build_full_context(self, model_name: str) -> str:
        """Build the biggest possible workspace dump that fits the model's context.
        Every file that fits is included IN FULL — no summaries, no truncation
        until we actually run out of budget."""
        if not self.analysis:
            self.analyze()

        budget = get_context_budget(model_name)
        parts: list[str] = []
        chars = 0

        # 1. Overview
        overview = self._build_overview()
        parts.append(overview)
        chars += len(overview)

        # 2. Full directory tree
        tree = f"\n\n=== DIRECTORY STRUCTURE ===\n{self.analysis['directory_tree']}\n"
        parts.append(tree)
        chars += len(tree)

        ordering_note = (
            "\n\n=== FILE ORDER ===\n"
            "Files below are listed NEWEST-FIRST (most recently modified at the top). "
            "Treat files near the top as the latest intent of the project.\n"
        )
        parts.append(ordering_note)
        chars += len(ordering_note)

        # 3. Every file, full content, newest first
        files_included = 0
        files_truncated = 0
        files_skipped = []

        for f in self.files:
            if f["content"] is None:
                continue

            remaining = budget - chars
            if remaining < 300:
                files_skipped.append(f["path"])
                continue

            header = (
                f"\n\n=== FILE: {f['path']} ({f['size']} bytes, "
                f"{f['language'] or f['ext']}, last modified {_fmt_mtime(f.get('mtime', 0.0))}) ===\n"
            )
            content = f["content"]
            block = header + content + "\n"

            if len(block) <= remaining:
                # Fits fully
                parts.append(block)
                chars += len(block)
                files_included += 1
            elif remaining > 1000:
                # Partial fit — include what we can
                available = remaining - len(header) - 60
                parts.append(header + content[:available] + f"\n... (truncated, {len(content) - available} chars remaining)\n")
                chars += remaining
                files_truncated += 1
            else:
                files_skipped.append(f["path"])

        # 4. Note about skipped files
        if files_skipped:
            note = f"\n\n=== FILES NOT INCLUDED (context limit reached) ===\n"
            note += "\n".join(f"  - {p}" for p in files_skipped[:50])
            if len(files_skipped) > 50:
                note += f"\n  ... and {len(files_skipped) - 50} more"
            parts.append(note)

        logging.info(
            "Context built for %s: %d files full, %d truncated, %d skipped, %d/%d chars used",
            model_name, files_included, files_truncated, len(files_skipped), chars, budget,
        )

        return "".join(parts)

    def build_judge_context(self, model_name: str) -> tuple[str, str]:
        """Build (structure_summary, key_files_content) for the judge bootstrapper.
        Judge gets half the model's budget — the other half is for the judge prompt itself."""
        if not self.analysis:
            self.analyze()

        structure = self._build_overview() + "\n\n" + self.analysis["directory_tree"]
        budget = get_context_budget(model_name) // 2
        parts: list[str] = [
            "FILE ORDER: newest-first (most recently modified at the top). "
            "Treat files near the top as the latest intent.\n"
        ]
        chars = len(parts[0])

        for f in self.files:
            if f["content"] is None:
                continue
            remaining = budget - chars
            if remaining < 300:
                break
            mtime_label = _fmt_mtime(f.get("mtime", 0.0))
            block = f"\nFILE: {f['path']} (last modified {mtime_label})\n---\n{f['content']}\n---\n"
            if len(block) <= remaining:
                parts.append(block)
                chars += len(block)
            elif remaining > 800:
                available = remaining - 100
                parts.append(
                    f"\nFILE: {f['path']} (last modified {mtime_label})\n---\n"
                    f"{f['content'][:available]}\n... (truncated)\n---\n"
                )
                chars += remaining
                break

        return structure, "".join(parts)

    # ── API response ──

    def to_api_response(self) -> dict[str, Any]:
        """JSON-safe response for the frontend (no file contents)."""
        if not self.analysis:
            self.analyze()
        return {
            "workspace_dir": self.analysis["workspace_dir"],
            "total_files": self.analysis["total_files"],
            "code_files": self.analysis["code_files"],
            "total_size": self.analysis["total_size"],
            "total_dirs": self.analysis["total_dirs"],
            "languages": self.analysis["languages"],
            "primary_language": self.analysis["primary_language"],
            "project_type": self.analysis["project_type"],
            "entry_points": self.analysis["entry_points"],
            "data_files": self.analysis["data_files"],
            "test_files": self.analysis["test_files"],
            "config_files": self.analysis["config_files"],
            "dependencies": self.analysis["dependencies"],
            "directory_tree": self.analysis["directory_tree"],
            "suggested_goals": self.analysis["suggested_goals"],
            "file_list": [
                {"path": f["path"], "size": f["size"], "language": f["language"]}
                for f in self.files if f["is_code"]
            ],
        }

    # ── Internal helpers ──

    def _build_overview(self) -> str:
        a = self.analysis
        lines = [
            "=== PROJECT OVERVIEW ===",
            f"Workspace: {a['workspace_dir']}",
            f"Type: {a['project_type']}",
            f"Primary language: {a['primary_language']}",
            f"Files: {a['total_files']} total, {a['code_files']} code files across {a['total_dirs']} directories",
            f"Size: {a['total_size']:,} bytes",
            f"Languages: {', '.join(f'{k} ({v})' for k, v in a['languages'].items())}",
        ]
        if a["entry_points"]:
            lines.append(f"Entry points: {', '.join(a['entry_points'][:8])}")
        if a["dependencies"]:
            lines.append(f"Dependencies: {', '.join(a['dependencies'][:20])}")
        if a["data_files"]:
            lines.append(f"Data files: {', '.join(a['data_files'][:8])}")
        return "\n".join(lines)

    def _build_tree(self) -> str:
        lines: list[str] = []

        dir_files: dict[str, list[str]] = {}
        for f in self.files:
            parent = os.path.dirname(f["path"]) or "."
            parent = parent.replace("\\", "/")
            dir_files.setdefault(parent, []).append(f["name"])

        # Root files
        for name in dir_files.get(".", []):
            lines.append(name)

        # Walk directories
        seen_dirs: set[str] = set()
        for f in self.files:
            parts = f["path"].replace("\\", "/").split("/")
            if len(parts) <= 1:
                continue
            # Build each level
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if d not in seen_dirs:
                    seen_dirs.add(d)
                    indent = "  " * (i - 1)
                    lines.append(f"{indent}{parts[i-1]}/")
                    # Add files in this directory
                    for fname in dir_files.get(d, []):
                        lines.append(f"{indent}  {fname}")

        return "\n".join(lines[:300])

    def _detect_project_type(self) -> str:
        names = {f["name"].lower() for f in self.files}
        paths = {f["path"].lower() for f in self.files}
        sample = " ".join((f.get("content") or "")[:500] for f in self.files[:20]).lower()

        if any(kw in p for p in paths for kw in ("trading", "trade", "polymarket", "binance", "broker")):
            return "trading_bot"
        if "manage.py" in names and "settings.py" in names:
            return "django_app"
        if any(kw in sample for kw in ("tensorflow", "torch", "sklearn", "keras", "xgboost")):
            return "ml_project"
        if "package.json" in names:
            return "node_app"
        if "app.py" in names or "server.py" in names:
            if "fastapi" in sample:
                return "fastapi_app"
            if "flask" in sample:
                return "flask_app"
            return "web_app"
        if any(f["ext"] == ".py" for f in self.files):
            return "python_project"
        return "generic_project"

    def _extract_dependencies(self) -> list[str]:
        deps = []
        for f in self.files:
            if f["name"] == "requirements.txt" and f["content"]:
                for line in f["content"].splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-"):
                        pkg = line.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
                        if pkg:
                            deps.append(pkg)
                return deps
            if f["name"] == "pyproject.toml" and f["content"]:
                in_deps = False
                for line in f["content"].splitlines():
                    if "dependencies" in line and "=" in line:
                        in_deps = True
                        continue
                    if in_deps:
                        if line.strip().startswith("]"):
                            break
                        cleaned = line.strip().strip('",').strip()
                        if cleaned:
                            deps.append(cleaned.split(">=")[0].split("==")[0].split("<")[0].strip())
                return deps
        return deps

    def _suggest_goals(self) -> list[dict[str, str]]:
        if not self.files:
            return []

        suggestions = []
        ptype = self._detect_project_type()
        has_tests = any(f["name"].startswith("test_") for f in self.files)

        if ptype == "trading_bot":
            suggestions.append({
                "goal": "Improve the trading strategy to achieve a win-rate above 80% on backtested data",
                "judge_brief": "Backtest on historical data, measure win-rate, risk-adjusted returns, and max drawdown",
            })
            suggestions.append({
                "goal": "Add risk management — position sizing, stop-losses, and drawdown limits",
                "judge_brief": "Verify risk controls are enforced, max drawdown stays under threshold",
            })
        elif ptype == "ml_project":
            suggestions.append({
                "goal": "Improve model accuracy and reduce overfitting on validation data",
                "judge_brief": "Measure validation accuracy, train/val gap, and generalization metrics",
            })
        elif ptype in ("web_app", "fastapi_app", "flask_app", "django_app"):
            suggestions.append({
                "goal": "Add error handling, input validation, and improve API robustness",
                "judge_brief": "Test edge cases, invalid inputs, error responses, and recovery",
            })

        if not has_tests:
            suggestions.append({
                "goal": "Add comprehensive unit tests for the core modules",
                "judge_brief": "Score based on test coverage, edge cases, and all tests passing",
            })

        suggestions.append({
            "goal": "Fix bugs and improve code quality across the project",
            "judge_brief": "Verify bug fixes, no regressions, improved error handling",
        })

        return suggestions
