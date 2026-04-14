import logging
import os
from itertools import islice
from typing import Iterable


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
    }

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self._atomic_writes_enabled = True
        os.makedirs(self.workspace_dir, exist_ok=True)

    def build_context(
        self,
        include_paths: Iterable[str] | None = None,
        max_files: int = 12,
        max_chars: int = 28000,
    ) -> str:
        snippets = ["WORKSPACE CONTEXT:"]
        chars_used = len(snippets[0])
        selected_paths = list(self._collect_candidate_files(include_paths))[:max_files]

        for path in selected_paths:
            content = self.read_text(path)
            if not content:
                continue

            available = max_chars - chars_used
            if available <= 0:
                break

            relative_path = os.path.relpath(path, self.workspace_dir)
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

    def _collect_candidate_files(self, include_paths: Iterable[str] | None):
        if include_paths:
            for raw_path in include_paths:
                path = raw_path if os.path.isabs(raw_path) else os.path.join(self.workspace_dir, raw_path)
                if os.path.isfile(path):
                    yield os.path.abspath(path)
            return

        for root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [d for d in dirs if d not in self.SKIP_DIRS and not d.startswith(".")]

            for filename in sorted(files):
                if not filename.endswith(self.SUPPORTED_EXTENSIONS):
                    continue
                path = os.path.join(root, filename)
                if os.path.getsize(path) > 50_000:
                    continue
                yield path
