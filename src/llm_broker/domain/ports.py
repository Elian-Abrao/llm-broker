from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol

from .agent import AgentSession, ToolDescriptor, ToolResult
from .auth import AuthSession, CallbackPayload, OAuthLoginTicket


class SessionStorePort(Protocol):
    def load(self) -> AuthSession | None: ...

    def save(self, session: AuthSession) -> None: ...

    def clear(self) -> None: ...


class CallbackServerHandlePort(Protocol):
    def start(self) -> None: ...

    def close(self) -> None: ...


class CallbackServerFactoryPort(Protocol):
    def create(
        self,
        *,
        expected_state: str,
        success_title: str,
        success_message: str,
        on_callback: Callable[[CallbackPayload], None],
    ) -> CallbackServerHandlePort: ...


class OAuthGatewayPort(Protocol):
    def create_login_ticket(self, *, now_ms: int, timeout_ms: int) -> OAuthLoginTicket: ...

    def exchange_authorization_code(
        self,
        *,
        code: str,
        ticket: OAuthLoginTicket,
        now_ms: int,
    ) -> AuthSession: ...

    def refresh_session(self, *, session: AuthSession, now_ms: int) -> AuthSession: ...


class LLMGatewayPort(Protocol):
    """Provider-agnostic LLM gateway. Every provider must implement this."""

    def stream_chat(
        self,
        *,
        request_id: str,
        session: AuthSession,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider_params: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]: ...


# Backward-compat alias
CodexGatewayPort = LLMGatewayPort


class AgentToolPort(Protocol):
    @property
    def descriptor(self) -> ToolDescriptor: ...

    def execute(self, *, session: AgentSession, input_payload: Any) -> ToolResult: ...
