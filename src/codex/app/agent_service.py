from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Generator, Iterator

from ..domain.agent import (
    AgentAction,
    AgentEvent,
    AgentSession,
    ToolDescriptor,
    build_agent_runtime_instructions,
    normalize_approval_policy,
    normalize_permission_profile,
    normalize_session_mode,
    parse_tool_call,
)
from ..domain.codex import normalize_codex_model, normalize_reasoning_effort
from ..domain.errors import BrokerError
from ..domain.ports import AgentToolPort
from .chat_service import ChatService


class AgentService:
    MAX_TOOL_ROUNDS = 4

    def __init__(
        self,
        *,
        chat_service: ChatService,
        tools: list[AgentToolPort],
        now=None,
        workspace_root: Path | None = None,
    ) -> None:
        self._chat_service = chat_service
        self._tools = {tool.descriptor.name: tool for tool in tools}
        self._now = now or (lambda: int(time.time() * 1000))
        self._default_workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self._sessions: dict[str, AgentSession] = {}

    def create_session(
        self,
        *,
        mode: str = "agent",
        model: str | None = None,
        reasoning_effort: str | None = None,
        permission_profile: str | None = None,
        approval_policy: str | None = None,
        cwd: str | None = None,
    ) -> AgentSession:
        now_ms = self._now()
        workspace_root = self._default_workspace_root
        resolved_cwd = self._resolve_cwd_for_new_session(workspace_root, cwd)
        session = AgentSession(
            id=str(uuid.uuid4()),
            mode=normalize_session_mode(mode),
            model=normalize_codex_model(model),
            reasoning_effort=normalize_reasoning_effort(reasoning_effort),
            permission_profile=normalize_permission_profile(permission_profile),
            approval_policy=normalize_approval_policy(approval_policy),
            workspace_root=str(workspace_root),
            cwd=str(resolved_cwd),
            created_at=now_ms,
            updated_at=now_ms,
        )
        self._sessions[session.id] = session
        return session

    def get_session(self, session_id: str) -> AgentSession:
        session = self._sessions.get(session_id)
        if not session:
            raise BrokerError(404, f"Unknown session: {session_id}")
        return session

    def list_tools(self) -> list[dict[str, object]]:
        return [tool.descriptor.to_dict() for tool in self._tools.values()]

    def get_session_snapshot(self, session_id: str) -> dict[str, object]:
        return self.get_session(session_id).to_dict()

    def reset_session(self, session_id: str) -> AgentSession:
        session = self.get_session(session_id)
        session.messages.clear()
        session.pending_action = None
        session.touch(self._now())
        return session

    def set_permissions(self, session_id: str, permission_profile: str) -> AgentSession:
        session = self.get_session(session_id)
        session.permission_profile = normalize_permission_profile(permission_profile)
        session.touch(self._now())
        return session

    def set_approval_policy(self, session_id: str, approval_policy: str) -> AgentSession:
        session = self.get_session(session_id)
        session.approval_policy = normalize_approval_policy(approval_policy)
        session.touch(self._now())
        return session

    def set_model(self, session_id: str, model: str) -> AgentSession:
        session = self.get_session(session_id)
        session.model = normalize_codex_model(model)
        session.touch(self._now())
        return session

    def set_reasoning(self, session_id: str, reasoning_effort: str) -> AgentSession:
        session = self.get_session(session_id)
        session.reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        session.touch(self._now())
        return session

    def set_cwd(self, session_id: str, cwd: str) -> AgentSession:
        session = self.get_session(session_id)
        session.cwd = str(self._resolve_session_path(session, cwd))
        session.touch(self._now())
        return session

    def approve_action(self, session_id: str, action_id: str) -> Iterator[dict[str, object]]:
        session = self.get_session(session_id)
        action = session.pending_action
        if not action or action.id != action_id:
            yield self._event(session, "error", "There is no matching pending action to approve.", {"statusCode": 404})
            return

        session.pending_action = None
        session.touch(self._now())
        yield self._event(
            session,
            "action.approved",
            f"Approved `{action.tool_name}`.",
            {"action": {**action.to_dict(), "status": "approved"}},
        )

        tool_completed = yield from self._execute_tool_and_emit(
            session,
            tool_name=action.tool_name,
            input_payload=action.input_payload,
        )
        if not tool_completed:
            return

        yield from self._continue_turn(session, start_round=action.next_round_index)

    def reject_action(self, session_id: str, action_id: str, reason: str | None = None) -> Iterator[dict[str, object]]:
        session = self.get_session(session_id)
        action = session.pending_action
        if not action or action.id != action_id:
            yield self._event(session, "error", "There is no matching pending action to reject.", {"statusCode": 404})
            return

        rejection_reason = (reason or "").strip() or "Rejected by operator."
        session.pending_action = None
        session.messages.append(
            {
                "role": "system",
                "content": f"Local tool request `{action.tool_name}` was rejected by the operator. Reason: {rejection_reason}",
            }
        )
        session.touch(self._now())
        yield self._event(
            session,
            "action.rejected",
            f"Rejected `{action.tool_name}`.",
            {
                "action": {**action.to_dict(), "status": "rejected"},
                "reason": rejection_reason,
            },
        )
        yield from self._continue_turn(session, start_round=action.next_round_index)

    def execute_tool(self, session_id: str, tool_name: str, input_payload: Any) -> Iterator[dict[str, object]]:
        session = self.get_session(session_id)
        yield from self._execute_tool(session, tool_name, input_payload)

    def send_turn(self, session_id: str, prompt: str) -> Iterator[dict[str, object]]:
        session = self.get_session(session_id)
        if not prompt.strip():
            yield self._event(session, "error", "Prompt cannot be empty.", {"statusCode": 400})
            return
        if session.pending_action:
            yield self._event(
                session,
                "error",
                "A pending action must be approved or rejected before sending a new turn.",
                {"statusCode": 409, "action": session.pending_action.to_dict()},
            )
            return

        session.messages.append({"role": "user", "content": prompt.strip()})
        session.touch(self._now())
        yield self._event(
            session,
            "turn.started",
            "Sending prompt to Codex.",
            {
                "mode": session.mode,
                "model": session.model,
                "reasoningEffort": session.reasoning_effort,
            },
        )

        yield from self._continue_turn(session, start_round=0)

    def _resolve_cwd_for_new_session(self, workspace_root: Path, cwd: str | None) -> Path:
        if not cwd:
            return workspace_root
        candidate = Path(cwd).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        return candidate.resolve()

    def _resolve_session_path(self, session: AgentSession, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = Path(session.cwd) / candidate
        resolved = candidate.resolve()

        if session.permission_profile != "full-access":
            workspace = Path(session.workspace_root).resolve()
            try:
                resolved.relative_to(workspace)
            except ValueError as exc:
                raise BrokerError(403, "Path is outside the active workspace.") from exc

        if not resolved.exists():
            raise BrokerError(404, f"Path does not exist: {resolved}")
        if not resolved.is_dir():
            raise BrokerError(400, f"Path is not a directory: {resolved}")
        return resolved

    def _run_model_round(self, session: AgentSession) -> Generator[dict[str, object], None, str | None]:
        request_messages = [
            {
                "role": "system",
                "content": build_agent_runtime_instructions(
                    session=session,
                    tools=self._tools_for_session(session),
                ),
            },
            *session.messages,
        ]

        output_chunks: list[str] = []
        for event in self._chat_service.stream_chat(
            {
                "executionMode": "agent",
                "model": session.model,
                "reasoningEffort": session.reasoning_effort,
                "messages": request_messages,
            }
        ):
            kind = str(event.get("kind") or "")
            if kind == "status":
                yield self._event(
                    session,
                    "model.status",
                    str(event.get("message") or "Connecting to Codex..."),
                )
                continue
            if kind == "delta" and isinstance(event.get("delta"), str):
                output_chunks.append(event["delta"])
                continue
            if kind == "error":
                yield self._event(
                    session,
                    "error",
                    str(event.get("message") or "Codex returned an error."),
                    {"statusCode": 502},
                )
                return None
        return "".join(output_chunks)

    def _tools_for_session(self, session: AgentSession):
        visible = []
        for tool in self._tools.values():
            descriptor = tool.descriptor
            if descriptor.requires_full_access and session.permission_profile != "full-access":
                continue
            if descriptor.requires_write and session.permission_profile == "read-only":
                continue
            visible.append(descriptor)
        return visible

    def _continue_turn(self, session: AgentSession, *, start_round: int) -> Iterator[dict[str, object]]:
        for round_index in range(start_round, self.MAX_TOOL_ROUNDS):
            output_text: str | None = None
            try:
                output_text = yield from self._run_model_round(session)
            except BrokerError as exc:
                yield self._event(session, "error", str(exc), {"statusCode": exc.status_code})
                return

            if output_text is None:
                return

            tool_call = parse_tool_call(output_text) if session.mode == "agent" else None
            if not tool_call:
                session.messages.append({"role": "assistant", "content": output_text})
                session.touch(self._now())
                if output_text:
                    yield self._event(session, "model.delta", output_text)
                yield self._event(
                    session,
                    "turn.completed",
                    "Codex responded.",
                    {
                        "outputText": output_text,
                        "session": session.to_dict(),
                    },
                )
                return

            tool = self._tools.get(tool_call.tool_name)
            if not tool:
                yield self._event(
                    session,
                    "error",
                    f"Unknown tool: {tool_call.tool_name}",
                    {"statusCode": 404, "tool": tool_call.tool_name},
                )
                return

            descriptor = tool.descriptor
            if self._requires_approval(session, descriptor):
                action = AgentAction(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    tool_name=tool_call.tool_name,
                    input_payload=tool_call.input_payload,
                    status="pending",
                    created_at=self._now(),
                    next_round_index=round_index + 1,
                    tool_requires_write=descriptor.requires_write,
                    tool_requires_full_access=descriptor.requires_full_access,
                )
                session.pending_action = action
                session.touch(self._now())
                yield self._event(
                    session,
                    "action.required",
                    f"Approval required for `{tool_call.tool_name}`.",
                    {"action": action.to_dict(), "session": session.to_dict()},
                )
                return

            yield self._event(
                session,
                "tool.requested",
                f"Model requested `{tool_call.tool_name}`.",
                {"tool": tool_call.tool_name},
            )
            tool_completed = yield from self._execute_tool_and_emit(
                session,
                tool_name=tool_call.tool_name,
                input_payload=tool_call.input_payload,
            )
            if not tool_completed:
                return

        yield self._event(
            session,
            "error",
            "The agent reached the maximum number of local tool rounds for this turn.",
            {"statusCode": 409},
        )

    def _execute_tool(self, session: AgentSession, tool_name: str, input_payload: Any) -> Iterator[dict[str, object]]:
        yield from self._execute_tool_and_emit(session, tool_name=tool_name, input_payload=input_payload)

    def _execute_tool_and_emit(self, session: AgentSession, *, tool_name: str, input_payload: Any) -> Iterator[dict[str, object] | bool]:
        tool = self._tools.get(tool_name)
        if not tool:
            yield self._event(session, "error", f"Unknown tool: {tool_name}", {"statusCode": 404, "tool": tool_name})
            return False

        yield self._event(
            session,
            "tool.started",
            f"Running `{tool_name}` locally.",
            {"tool": tool_name},
        )

        try:
            result = tool.execute(session=session, input_payload=input_payload)
        except BrokerError as exc:
            yield self._event(
                session,
                "error",
                str(exc),
                {"statusCode": exc.status_code, "tool": tool_name},
            )
            return False
        except Exception as exc:  # pragma: no cover
            yield self._event(
                session,
                "error",
                f"Tool `{tool_name}` failed: {exc}",
                {"statusCode": 500, "tool": tool_name},
            )
            return False

        session.messages.append({"role": "system", "content": result.to_context_message()})
        session.touch(self._now())
        yield self._event(
            session,
            "tool.output",
            result.output_text,
            {
                "tool": tool_name,
                "metadata": result.metadata,
            },
        )
        yield self._event(
            session,
            "tool.completed",
            f"`{tool_name}` finished.",
            {
                "tool": tool_name,
                "session": session.to_dict(),
            },
        )
        return True

    def _requires_approval(self, session: AgentSession, descriptor: ToolDescriptor) -> bool:
        policy = session.approval_policy
        if policy == "auto":
            return False
        if policy == "auto-edit":
            return descriptor.requires_write or descriptor.requires_full_access
        return True

    def _event(
        self,
        session: AgentSession,
        kind: str,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        session.event_sequence += 1
        event = AgentEvent(
            session_id=session.id,
            kind=kind,
            message=message,
            data={
                "eventId": str(uuid.uuid4()),
                "createdAt": self._now(),
                "sequence": session.event_sequence,
                **(data or {}),
            },
        )
        return event.to_dict()
