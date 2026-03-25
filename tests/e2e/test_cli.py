from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class CliE2ETests(unittest.TestCase):
    def _run_cli(
        self,
        *args: str,
        input_text: str | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        env["CODEX_BRIDGE_DISABLE_KEYRING"] = "1"
        env["CODEX_BRIDGE_AUTH_STORE_PATH"] = str(Path(temp_dir.name) / "auth" / "session.json")
        return subprocess.run(
            [sys.executable, "-m", "codex", *args],
            cwd=str(cwd or REPO_ROOT),
            env=env,
            text=True,
            capture_output=True,
            input=input_text,
            check=True,
        )

    def test_status_command_runs_from_module_entrypoint(self) -> None:
        result = self._run_cli("--json", "status")
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {"isRefreshing": False})

    def test_whoami_reports_unauthenticated_state(self) -> None:
        result = self._run_cli("--json", "whoami")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["authenticated"], False)
        self.assertEqual(payload["provider"], "codex")

    def test_doctor_outputs_structured_report(self) -> None:
        result = self._run_cli("--json", "doctor")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["service"], "codex-bridge")
        self.assertEqual(payload["auth"]["authenticated"], False)
        self.assertIn("authStorePath", payload["config"])

    def test_version_command_prints_package_version(self) -> None:
        result = self._run_cli("version")
        self.assertIn("codex-bridge", result.stdout)

    def test_interactive_chat_supports_logout_command(self) -> None:
        result = self._run_cli("chat", "--interactive", input_text="/logout\n")
        self.assertIn("Interactive chat", result.stdout)
        self.assertIn("/logout", result.stdout)
        self.assertIn("Session cleared. Exiting interactive chat.", result.stdout)

    def test_agent_interactive_lists_tools(self) -> None:
        result = self._run_cli("agent", input_text="/tools\n/exit\n")
        self.assertIn("Interactive agent", result.stdout)
        self.assertIn("read_file", result.stdout)
        self.assertIn("write_file", result.stdout)
        self.assertIn("shell", result.stdout)

    def test_agent_interactive_can_switch_permissions_and_use_workspace_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            result = self._run_cli(
                "agent",
                input_text="/permissions workspace-write\n/write notes.txt hello\n/read notes.txt\n/exit\n",
                cwd=workspace,
            )
            self.assertIn("Permissions set to workspace-write", result.stdout)
            self.assertIn("Wrote", result.stdout)
            self.assertIn("hello", result.stdout)


if __name__ == "__main__":
    unittest.main()
