from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ...domain.agent import AgentSession, ToolDescriptor, ToolResult
from ...domain.errors import BrokerError


class ShellTool:
    @property
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name="shell",
            description="Run a shell command in the current working directory.",
            requires_full_access=True,
        )

    def execute(self, *, session: AgentSession, input_payload: Any) -> ToolResult:
        if session.permission_profile != "full-access":
            raise BrokerError(403, "shell requires `full-access` permissions.")

        if not isinstance(input_payload, str) or not input_payload.strip():
            raise BrokerError(400, "shell requires a command string.")

        try:
            completed = subprocess.run(
                input_payload,
                shell=True,
                cwd=str(Path(session.cwd).resolve()),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise BrokerError(408, f"shell command timed out after {exc.timeout} seconds.") from exc
        output = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if stderr:
            output = f"{output}\n\nstderr:\n{stderr}".strip()
        if not output:
            output = "[no output]"

        return ToolResult(
            tool_name=self.descriptor.name,
            output_text=output,
            metadata={
                "cwd": str(Path(session.cwd).resolve()),
                "command": input_payload,
                "exitCode": completed.returncode,
            },
        )
