from __future__ import annotations

import uuid
from typing import Any, Iterator

from ..domain.errors import BrokerError
from .provider_registry import ProviderRegistry


class ChatService:
    def __init__(self, *, registry: ProviderRegistry) -> None:
        self._registry = registry

    def get_capabilities(self, provider_id: str | None = None) -> dict[str, object]:
        if provider_id:
            entry = self._registry.get(provider_id)
        else:
            entry = self._registry.get_default()
        session = entry.auth_service.get_state().session
        caps = dict(entry.capabilities)
        caps["authenticated"] = bool(session)
        caps["accountEmail"] = session.email if session else None
        return caps

    def get_all_capabilities(self) -> dict[str, object]:
        return {
            "providers": self._registry.list_capabilities(),
            "defaultProvider": self._registry.get_default().provider_id,
        }

    def _prepare_messages(self, request_payload: dict[str, Any]) -> list[dict[str, Any]]:
        messages = request_payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise BrokerError(400, "`messages` must contain at least one chat message.")
        return list(messages)

    def stream_chat(self, request_payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        raw_provider = request_payload.get("provider")
        provider_id = str(raw_provider).strip() if isinstance(raw_provider, str) and raw_provider.strip() else None

        entry = self._registry.get(provider_id) if provider_id else self._registry.get_default()
        messages = self._prepare_messages(request_payload)
        request_id = str(request_payload.get("requestId") or uuid.uuid4())
        model = str(request_payload.get("model") or "").strip() or entry.capabilities.get("defaultModel", "")
        raw_tools = request_payload.get("tools")
        tools = raw_tools if isinstance(raw_tools, list) else None
        raw_provider_params = request_payload.get("providerParams")
        provider_params = raw_provider_params if isinstance(raw_provider_params, dict) else None

        yield {
            "requestId": request_id,
            "provider": entry.provider_id,
            "kind": "status",
            "message": f"Connecting to {entry.provider_id}...",
        }

        session = entry.auth_service.get_valid_session()
        yield from entry.gateway.stream_chat(
            request_id=request_id,
            session=session,
            model=str(model),
            messages=messages,
            tools=tools,
            provider_params=provider_params,
        )

    def chat(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        request_id: str | None = None
        provider_id = "unknown"
        output_text = ""
        for event in self.stream_chat(request_payload):
            request_id = request_id or str(event.get("requestId"))
            provider_id = str(event.get("provider") or provider_id)
            if event.get("kind") == "delta" and isinstance(event.get("delta"), str):
                output_text += event["delta"]
            if event.get("kind") == "error":
                raise BrokerError(502, str(event.get("message") or "Provider returned an error."))

        if not request_id:
            raise BrokerError(502, "Bridge stream finished without a request id.")

        return {
            "requestId": request_id,
            "provider": provider_id,
            "outputText": output_text,
        }
