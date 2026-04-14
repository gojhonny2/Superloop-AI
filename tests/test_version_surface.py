import asyncio
import subprocess
import sys
import unittest

from api.server import configure_runtime_defaults, get_runtime_defaults, health, runtime_defaults
from superloop_version import RELEASE_CHANNEL, __version__


class VersionSurfaceTests(unittest.TestCase):
    def test_api_health_exposes_shared_version(self):
        payload = asyncio.run(health())
        self.assertEqual(payload["version"], __version__)
        self.assertEqual(payload["channel"], RELEASE_CHANNEL)

    def test_cli_version_uses_shared_version(self):
        result = subprocess.run(
            [sys.executable, "-m", "shells.app_cli", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn(__version__, result.stdout)
        self.assertIn(RELEASE_CHANNEL, result.stdout)

    def test_runtime_defaults_endpoint_exposes_configured_dashboard_defaults(self):
        original = get_runtime_defaults()
        try:
            configure_runtime_defaults(
                workspace_path="demo-workspace",
                target_score=0.91,
                manager_descriptor="openai:gpt-4o",
                specialist_descriptors=["anthropic:claude-3-5-sonnet-20240620", "gemini:gemini-1.5-pro"],
                resume=True,
            )
            payload = asyncio.run(runtime_defaults())
            defaults = payload["defaults"]
            self.assertEqual(defaults["workspace_path"], "demo-workspace")
            self.assertEqual(defaults["manager_descriptor"], "openai:gpt-4o")
            self.assertEqual(defaults["specialist_1_descriptor"], "anthropic:claude-3-5-sonnet-20240620")
            self.assertEqual(defaults["specialist_2_descriptor"], "gemini:gemini-1.5-pro")
            self.assertTrue(defaults["resume"])
            self.assertEqual(defaults["target_score"], 0.91)
        finally:
            configure_runtime_defaults(**original)
