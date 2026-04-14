import logging
import os
from itertools import islice
from typing import Iterable

from core.workspace_analyzer import is_windows_reserved_name, safe_relpath


class RuntimeWorkspace:
    """
    Curates model context from the project workspace and writes run artifacts atomically.
    """

    SUPPORTED_EXTENSIONS = (
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".html",
        ".css",
        ".md",
        ".txt",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".csv",
    )

    SKIP_DIRS = {
        ".git",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "runs",
        "catboost_info",
        "pytest-cache-files*",
        ".pytest_cache",
        ".mypy_cache",
    }

    # Files that are almost always important — prioritize these
    PRIORITY_NAMES = {
        "main.py", "app.py", "run.py", "run_agent.py", "agent.py",
        "config.py", "settings.py", "requirements.txt", "pyproject.toml",
        "package.json", "setup.py", "Dockerfile", "docker-compose.yml",
        "README.md", "CLAUDE.md", "Makefile",
    }

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self._atomic_writes_enabled = True
        os.makedirs(self.workspace_dir, exist_ok=True)

    def build_context(
        self,
        include_paths: Iterable[str] | None = None,
        max_files: int = 30,
        max_chars: int = 60000,
    ) -> str:
        snippets = ["WORKSPACE CONTEXT:"]
        chars_used = len(snippets[0])

        # First add the directory tree so the AI understands structure
        tree = self._build_tree(max_depth=3)
        tree_block = f"\nDIRECTORY STRUCTURE:\n---\n{tree}\n---\n"
        snippets.append(tree_block)
        chars_used += len(tree_block)

        selected_paths = list(self._collect_candidate_files(include_paths, max_files))

        for path in selected_paths:
            content = self.read_text(path)
            if not content:
                continue

            available = max_chars - chars_used
            if available <= 200:
                break

            relative_path = safe_relpath(path, self.workspace_dir)
            if not relative_path:
                continue
            trimmed = content[:available]
            block = f"\nFILE: {relative_path}\n---\n{trimmed}\n---\n"
            snippets.append(block)
            chars_used += len(block)

        return "".join(snippets)

    def build_structure_summary(self) -> str:
        """Build a concise summary of the workspace: tree + key file list."""
        tree = self._build_tree(max_depth=3)
        key_files = self._find_key_files()
        parts = [
            "WORKSPACE STRUCTURE:",
            tree,
            "",
            "KEY FILES:",
        ]
        for path in key_files[:20]:
            rel = safe_relpath(path, self.workspace_dir)
            if not rel:
                continue
            size = os.path.getsize(path)
            parts.append(f"  {rel} ({size} bytes)")
        return "\n".join(parts)

    def read_key_files(self, max_chars: int = 40000) -> str:
        """Read the most important files in the workspace, prioritized."""
        key_files = self._find_key_files()
        snippets = []
        chars_used = 0
        for path in key_files:
            content = self.read_text(path)
            if not content:
                continue
            available = max_chars - chars_used
            if available <= 200:
                break
            relative_path = safe_relpath(path, self.workspace_dir)
            if not relative_path:
                continue
            trimmed = content[:available]
            block = f"\nFILE: {relative_path}\n---\n{trimmed}\n---\n"
            snippets.append(block)
            chars_used += len(block)
        return "".join(snippets)

    def data_preview(self, data_path: str, lines: int = 12) -> str:
        if not data_path:
            return "NO EXTERNAL DATA OR BENCHMARK INPUT PROVIDED."
        if not os.path.exists(data_path):
            return "DATA OR INPUT FILE NOT FOUND."

        try:
            with open(data_path, "r", encoding="utf-8") as handle:
                return "".join(islice(handle, lines)).strip()
        except OSError as exc:
            return f"FAILED TO READ DATA SAMPLE: {exc}"

    def read_text(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except UnicodeDecodeError:
            logging.warning("Could not decode %s as utf-8.", path)
        except OSError as exc:
            logging.warning("Could not read %s: %s", path, exc)
        return ""

    def write_text(self, target_path: str, content: str) -> bool:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if not self._atomic_writes_enabled:
            return self._write_direct(target_path, content)

        temp_path = f"{target_path}.tmp"

        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(temp_path, target_path)
            return True
        except OSError as exc:
            self._atomic_writes_enabled = False
            logging.warning("Atomic writes are unavailable in this environment. Falling back to direct writes: %s", exc)
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return self._write_direct(target_path, content)

    def _write_direct(self, target_path: str, content: str) -> bool:
        try:
            with open(target_path, "w", encoding="utf-8") as handle:
                handle.write(content)
            return True
        except OSError as exc:
            logging.error("Failed to write %s: %s", target_path, exc)
            return False

    def _should_skip_dir(self, dirname: str) -> bool:
        if is_windows_reserved_name(dirname):
            return True
        if dirname.startswith("."):
            return True
        for pattern in self.SKIP_DIRS:
            if pattern.endswith("*"):
                if dirname.startswith(pattern[:-1]):
                    return True
            elif dirname == pattern:
                return True
        return False

    def _build_tree(self, max_depth: int = 3) -> str:
        """Build a directory tree string showing the workspace structure."""
        lines = []
        self._tree_walk(self.workspace_dir, lines, prefix="", depth=0, max_depth=max_depth)
        return "\n".join(lines[:150])  # cap at 150 lines

    def _tree_walk(self, dir_path: str, lines: list[str], prefix: str, depth: int, max_depth: int):
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return

        dirs = [
            e for e in entries
            if not is_windows_reserved_name(e)
            and os.path.isdir(os.path.join(dir_path, e))
            and not self._should_skip_dir(e)
        ]
        files = [
            e for e in entries
            if not is_windows_reserved_name(e)
            and os.path.isfile(os.path.join(dir_path, e))
        ]

        for f in files[:20]:  # cap files per dir
            lines.append(f"{prefix}{f}")
        if len(files) > 20:
            lines.append(f"{prefix}... and {len(files) - 20} more files")

        for d in dirs:
            lines.append(f"{prefix}{d}/")
            self._tree_walk(os.path.join(dir_path, d), lines, prefix + "  ", depth + 1, max_depth)

    def _find_key_files(self) -> list[str]:
        """Find the most important files in the workspace, sorted by priority."""
        priority_files = []
        regular_files = []

        for root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [d for d in dirs if not self._should_skip_dir(d)]

            for filename in sorted(files):
                if is_windows_reserved_name(filename):
                    continue
                if not filename.endswith(self.SUPPORTED_EXTENSIONS):
                    continue
                path = os.path.join(root, filename)
                if os.path.getsize(path) > 80_000:
                    continue

                relpath = safe_relpath(path, self.workspace_dir)
                if not relpath:
                    continue
                depth = relpath.count("/")

                if filename.lower() in self.PRIORITY_NAMES:
                    priority_files.append((depth, path))
                elif filename.endswith((".py", ".js", ".ts")):
                    regular_files.append((depth, path))

        # Sort by depth (shallower = more important), then by name
        priority_files.sort(key=lambda x: x[0])
        regular_files.sort(key=lambda x: x[0])

        return [p for _, p in priority_files] + [p for _, p in regular_files]

    def _collect_candidate_files(self, include_paths: Iterable[str] | None, max_files: int = 30):
        if include_paths:
            for raw_path in include_paths:
                path = raw_path if os.path.isabs(raw_path) else os.path.join(self.workspace_dir, raw_path)
                if os.path.isfile(path):
                    yield os.path.abspath(path)
            return

        key_files = self._find_key_files()
        for path in key_files[:max_files]:
            yield path
