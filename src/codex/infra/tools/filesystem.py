from __future__ import annotations

from pathlib import Path
from typing import Any

from ...domain.agent import AgentSession, ToolDescriptor, ToolResult
from ...domain.errors import BrokerError

MAX_FILE_BYTES = 32_000


def _workspace_root(session: AgentSession) -> Path:
    return Path(session.workspace_root).resolve()


def _session_cwd(session: AgentSession) -> Path:
    return Path(session.cwd).resolve()


def _resolve_session_path(session: AgentSession, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = _session_cwd(session) / candidate
    resolved = candidate.resolve()

    if session.permission_profile != "full-access":
        workspace = _workspace_root(session)
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise BrokerError(403, "Path is outside the active workspace.") from exc

    return resolved


class ReadFileTool:
    @property
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name="read_file",
            description="Read a UTF-8 text file from the current workspace.",
        )

    def execute(self, *, session: AgentSession, input_payload: Any) -> ToolResult:
        if not isinstance(input_payload, str) or not input_payload.strip():
            raise BrokerError(400, "read_file requires a path.")

        path = _resolve_session_path(session, input_payload.strip())
        if not path.exists():
            raise BrokerError(404, f"File not found: {path}")
        if not path.is_file():
            raise BrokerError(400, f"Path is not a file: {path}")

        data = path.read_bytes()
        truncated = len(data) > MAX_FILE_BYTES
        text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += "\n\n[truncated]"

        return ToolResult(
            tool_name=self.descriptor.name,
            output_text=text,
            metadata={
                "path": str(path),
                "bytesRead": min(len(data), MAX_FILE_BYTES),
                "truncated": truncated,
            },
        )


class WriteFileTool:
    @property
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name="write_file",
            description="Write UTF-8 text to a file inside the current workspace.",
            requires_write=True,
        )

    def execute(self, *, session: AgentSession, input_payload: Any) -> ToolResult:
        if session.permission_profile == "read-only":
            raise BrokerError(403, "write_file requires `workspace-write` or `full-access` permissions.")

        if not isinstance(input_payload, dict):
            raise BrokerError(400, "write_file requires an object with `path` and `content`.")

        raw_path = input_payload.get("path")
        content = input_payload.get("content")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise BrokerError(400, "write_file requires a non-empty `path`.")
        if not isinstance(content, str):
            raise BrokerError(400, "write_file requires string `content`.")

        path = _resolve_session_path(session, raw_path.strip())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        return ToolResult(
            tool_name=self.descriptor.name,
            output_text=f"Wrote {len(content.encode('utf-8'))} bytes to {path}.",
            metadata={
                "path": str(path),
                "bytesWritten": len(content.encode("utf-8")),
            },
        )
