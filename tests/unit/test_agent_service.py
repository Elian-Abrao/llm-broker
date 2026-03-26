from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from llm_broker.app.agent_service import AgentService
from llm_broker.infra.tools import ReadFileTool, ShellTool, WriteFileTool


class FakeChatService:
    def __init__(self, responses: list[list[dict[str, object]]] | None = None) -> None:
        self.requests: list[dict[str, object]] = []
        self.responses = responses or [
            [
                {"kind": "status", "message": "Connecting to codex..."},
                {"kind": "delta", "delta": "Hello"},
                {"kind": "delta", "delta": " world"},
            ]
        ]

    def stream_chat(self, request_payload: dict[str, object]):
        self.requests.append(request_payload)
        events = self.responses.pop(0) if self.responses else []
        for event in events:
            yield event


class AgentServiceTests(unittest.TestCase):
    def test_send_turn_emits_events_and_updates_context(self) -> None:
        service = AgentService(
            chat_service=FakeChatService(),
            tools=[ReadFileTool(), WriteFileTool(), ShellTool()],
            workspace_root=Path.cwd(),
            now=lambda: 1_000,
        )
        session = service.create_session(mode="agent", permission_profile="read-only")

        events = list(service.send_turn(session.id, "Say hello"))

        self.assertEqual(events[0]["kind"], "turn.started")
        self.assertEqual(events[1]["kind"], "model.status")
        self.assertEqual(events[2]["kind"], "model.delta")
        self.assertEqual(events[-1]["kind"], "turn.completed")
        self.assertEqual(events[-1]["outputText"], "Hello world")
        self.assertEqual(service.get_session(session.id).messages[-1]["content"], "Hello world")

    def test_send_turn_can_execute_model_requested_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "context.txt").write_text("agent context", encoding="utf-8")
            service = AgentService(
                chat_service=FakeChatService(
                    responses=[
                        [
                            {"kind": "status", "message": "Connecting to codex..."},
                            {
                                "kind": "delta",
                                "delta": '<tool_call>{"tool":"read_file","input":"context.txt"}</tool_call>',
                            },
                        ],
                        [
                            {"kind": "status", "message": "Connecting to codex..."},
                            {"kind": "delta", "delta": "I inspected the file and I am ready."},
                        ],
                    ]
                ),
                tools=[ReadFileTool(), WriteFileTool(), ShellTool()],
                workspace_root=workspace,
                now=lambda: 1_000,
            )
            session = service.create_session(mode="agent", permission_profile="read-only", approval_policy="auto")

            events = list(service.send_turn(session.id, "Inspect the file"))

            self.assertTrue(any(event["kind"] == "tool.requested" for event in events))
            self.assertTrue(any(event["kind"] == "tool.output" for event in events))
            self.assertEqual(events[-1]["kind"], "turn.completed")
            self.assertEqual(events[-1]["outputText"], "I inspected the file and I am ready.")
            self.assertIn("agent context", service.get_session(session.id).messages[-2]["content"])

    def test_manual_approval_creates_pending_action_and_resume_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "context.txt").write_text("agent context", encoding="utf-8")
            service = AgentService(
                chat_service=FakeChatService(
                    responses=[
                        [
                            {"kind": "status", "message": "Connecting to codex..."},
                            {
                                "kind": "delta",
                                "delta": '<tool_call>{"tool":"read_file","input":"context.txt"}</tool_call>',
                            },
                        ],
                        [
                            {"kind": "status", "message": "Connecting to codex..."},
                            {"kind": "delta", "delta": "Approved flow complete."},
                        ],
                    ]
                ),
                tools=[ReadFileTool(), WriteFileTool(), ShellTool()],
                workspace_root=workspace,
                now=lambda: 1_000,
            )
            session = service.create_session(mode="agent", permission_profile="read-only", approval_policy="manual")

            first_events = list(service.send_turn(session.id, "Inspect the file"))

            self.assertEqual(first_events[-1]["kind"], "action.required")
            self.assertEqual(service.get_session(session.id).status, "awaiting_approval")
            pending_action = service.get_session(session.id).pending_action
            assert pending_action is not None

            second_events = list(service.approve_action(session.id, pending_action.id))

            self.assertEqual(second_events[0]["kind"], "action.approved")
            self.assertTrue(any(event["kind"] == "tool.completed" for event in second_events))
            self.assertEqual(second_events[-1]["kind"], "turn.completed")
            self.assertEqual(second_events[-1]["outputText"], "Approved flow complete.")
            self.assertIsNone(service.get_session(session.id).pending_action)

    def test_workspace_write_can_write_and_read_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            service = AgentService(
                chat_service=FakeChatService(),
                tools=[ReadFileTool(), WriteFileTool(), ShellTool()],
                workspace_root=workspace,
                now=lambda: 1_000,
            )
            session = service.create_session(mode="agent", permission_profile="workspace-write")

            write_events = list(
                service.execute_tool(
                    session.id,
                    "write_file",
                    {"path": "notes.txt", "content": "hello from agent"},
                )
            )
            read_events = list(service.execute_tool(session.id, "read_file", "notes.txt"))

            self.assertEqual(write_events[-1]["kind"], "tool.completed")
            self.assertEqual(read_events[-2]["kind"], "tool.output")
            self.assertIn("hello from agent", read_events[-2]["message"])

    def test_shell_requires_full_access(self) -> None:
        service = AgentService(
            chat_service=FakeChatService(),
            tools=[ReadFileTool(), WriteFileTool(), ShellTool()],
            workspace_root=Path.cwd(),
            now=lambda: 1_000,
        )
        session = service.create_session(mode="agent", permission_profile="read-only")

        events = list(service.execute_tool(session.id, "shell", "pwd"))

        self.assertEqual(events[-1]["kind"], "error")
        self.assertIn("full-access", events[-1]["message"])


if __name__ == "__main__":
    unittest.main()
