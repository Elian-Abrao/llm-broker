from __future__ import annotations

import uuid
from typing import Any, Iterator

from ..domain.auth import AuthSession
from ..domain.codex import build_capabilities, normalize_codex_model, normalize_reasoning_effort
from ..domain.errors import BrokerError
from ..domain.ports import CodexGatewayPort
from ..infra.codex.default_instructions import CHAT_MODE_SYSTEM_MESSAGE
from .auth_service import AuthService


class ChatService:
    def __init__(self, *, auth_service: AuthService, codex_gateway: CodexGatewayPort) -> None:
        self._auth_service = auth_service
        self._codex_gateway = codex_gateway

    def get_capabilities(self) -> dict[str, object]:
        state = self._auth_service.get_state()
        session = state.session
        return build_capabilities(
            authenticated=bool(session),
            account_email=session.email if session else None,
        )

    def _prepare_messages(self, request_payload: dict[str, Any]) -> list[dict[str, Any]]:
        messages = request_payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise BrokerError(400, "`messages` must contain at least one chat message.")

        execution_mode = str(request_payload.get("executionMode") or "chat").strip().lower()
        if execution_mode == "agent":
            return messages

        return [
            {"role": "system", "content": CHAT_MODE_SYSTEM_MESSAGE},
            *messages,
        ]

    def stream_chat(self, request_payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        provider = str(request_payload.get("provider") or "codex").strip()
        if provider and provider != "codex":
            raise BrokerError(400, "This broker is Codex-only. Omit `provider` or set it to `codex`.")

        messages = self._prepare_messages(request_payload)
        request_id = str(request_payload.get("requestId") or uuid.uuid4())
        model = normalize_codex_model(request_payload.get("model") if isinstance(request_payload.get("model"), str) else None)
        reasoning_effort = normalize_reasoning_effort(
            request_payload.get("reasoningEffort") if isinstance(request_payload.get("reasoningEffort"), str) else None
        )

        yield {
            "requestId": request_id,
            "provider": "codex",
            "kind": "status",
            "message": "Connecting to codex...",
        }

        raw_tools = request_payload.get("tools")
        tools = raw_tools if isinstance(raw_tools, list) else None

        session = self._auth_service.get_valid_session()
        yield from self._codex_gateway.stream_chat(
            request_id=request_id,
            session=session,
            model=model,
            reasoning_effort=reasoning_effort,
            messages=messages,
            tools=tools,
        )

    def chat(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        request_id: str | None = None
        output_text = ""
        model = normalize_codex_model(request_payload.get("model") if isinstance(request_payload.get("model"), str) else None)
        for event in self.stream_chat(request_payload):
            request_id = request_id or str(event.get("requestId"))
            if event.get("kind") == "delta" and isinstance(event.get("delta"), str):
                output_text += event["delta"]
            if event.get("kind") == "error":
                raise BrokerError(502, str(event.get("message") or "Codex returned an error."))

        if not request_id:
            raise BrokerError(502, "Bridge stream finished without a request id.")

        return {
            "requestId": request_id,
            "provider": "codex",
            "model": model,
            "outputText": output_text,
        }
