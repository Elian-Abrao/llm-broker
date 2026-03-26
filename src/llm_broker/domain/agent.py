from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


VALID_PERMISSION_PROFILES = ("read-only", "workspace-write", "full-access")
VALID_SESSION_MODES = ("chat", "agent")
VALID_APPROVAL_POLICIES = ("manual", "auto-edit", "auto")
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def normalize_permission_profile(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in VALID_PERMISSION_PROFILES:
        return normalized
    return "read-only"


def normalize_session_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in VALID_SESSION_MODES:
        return normalized
    return "agent"


def normalize_approval_policy(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in VALID_APPROVAL_POLICIES:
        return normalized
    return "manual"


@dataclass(slots=True)
class AgentEvent:
    session_id: str
    kind: str
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sessionId": self.session_id,
            "kind": self.kind,
        }
        if self.message:
            payload["message"] = self.message
        if self.data:
            payload.update(self.data)
        return payload


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    description: str
    requires_write: bool = False
    requires_full_access: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "requiresWrite": self.requires_write,
            "requiresFullAccess": self.requires_full_access,
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_name: str
    input_payload: Any


@dataclass(slots=True)
class AgentAction:
    id: str
    session_id: str
    tool_name: str
    input_payload: Any
    status: str
    created_at: int
    next_round_index: int
    tool_requires_write: bool = False
    tool_requires_full_access: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "tool": self.tool_name,
            "input": self.input_payload,
            "status": self.status,
            "createdAt": self.created_at,
            "nextRoundIndex": self.next_round_index,
            "requiresWrite": self.tool_requires_write,
            "requiresFullAccess": self.tool_requires_full_access,
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    output_text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_context_message(self) -> str:
        lines = [f"Tool `{self.tool_name}` executed locally."]
        path = self.metadata.get("path")
        if isinstance(path, str) and path:
            lines.append(f"Path: {path}")
        exit_code = self.metadata.get("exitCode")
        if isinstance(exit_code, int):
            lines.append(f"Exit code: {exit_code}")
        if self.output_text:
            lines.append("Output:")
            lines.append(self.output_text)
        return "\n".join(lines)


@dataclass(slots=True)
class AgentSession:
    id: str
    mode: str
    model: str
    reasoning_effort: str
    permission_profile: str
    approval_policy: str
    workspace_root: str
    cwd: str
    created_at: int
    updated_at: int
    messages: list[dict[str, str]] = field(default_factory=list)
    pending_action: AgentAction | None = None
    event_sequence: int = 0

    def touch(self, updated_at: int) -> None:
        self.updated_at = updated_at

    @property
    def status(self) -> str:
        return "awaiting_approval" if self.pending_action else "idle"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "mode": self.mode,
            "model": self.model,
            "reasoningEffort": self.reasoning_effort,
            "permissionProfile": self.permission_profile,
            "approvalPolicy": self.approval_policy,
            "workspaceRoot": self.workspace_root,
            "cwd": self.cwd,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "messageCount": len(self.messages),
            "status": self.status,
            "eventSequence": self.event_sequence,
        }
        if self.pending_action:
            payload["pendingAction"] = self.pending_action.to_dict()
        return payload


def parse_tool_call(text: str) -> ToolCall | None:
    match = TOOL_CALL_RE.search(text.strip())
    if not match:
        return None

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    tool_name = payload.get("tool")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None

    return ToolCall(
        tool_name=tool_name.strip(),
        input_payload=payload.get("input"),
    )


def build_agent_runtime_instructions(*, session: AgentSession, tools: list[ToolDescriptor]) -> str:
    tool_lines = []
    for tool in tools:
        suffix: list[str] = []
        if tool.requires_write:
            suffix.append("requires workspace-write")
        if tool.requires_full_access:
            suffix.append("requires full-access")
        details = f" ({', '.join(suffix)})" if suffix else ""
        tool_lines.append(f"- {tool.name}: {tool.description}{details}")

    return "\n".join(
        [
            "## Local agent runtime",
            f"- Mode: {session.mode}",
            f"- Permissions: {session.permission_profile}",
            f"- Workspace root: {session.workspace_root}",
            f"- Current working directory: {session.cwd}",
            "",
            "## Available local tools",
            *tool_lines,
            "",
            "## Tool call protocol",
            "- If you need a local tool, reply with exactly one XML block and no markdown fence.",
            '- Format: <tool_call>{"tool":"read_file","input":"README.md"}</tool_call>',
            '- The `input` field may be a string or an object, depending on the tool.',
            "- After a tool runs, tool output will be added back into the conversation as local context.",
            "- If no tool is needed, answer normally.",
        ]
    )
