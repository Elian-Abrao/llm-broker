from __future__ import annotations

from types import SimpleNamespace
import unittest

from llm_broker.app.chat_service import ChatService
from llm_broker.app.provider_registry import ProviderEntry, ProviderRegistry
from llm_broker.domain.auth import AuthSession


class FakeAuthService:
    def __init__(self) -> None:
        self.session = AuthSession(
            provider="codex",
            access_token="access",
            refresh_token="refresh",
            expires_at=9_999_999,
            updated_at=1_000,
            email="user@example.com",
        )

    def get_state(self):
        return SimpleNamespace(session=self.session)

    def get_valid_session(self) -> AuthSession:
        return self.session


class FakeGateway:
    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    def stream_chat(self, *, request_id, session, model, messages, tools=None, provider_params=None):
        self.last_kwargs = {
            "request_id": request_id,
            "messages": messages,
            "model": model,
            "provider_params": provider_params,
        }
        yield {"requestId": request_id, "provider": "codex", "kind": "delta", "delta": "OK"}
        yield {"requestId": request_id, "provider": "codex", "kind": "done"}


def _make_service(gateway: FakeGateway) -> tuple[ChatService, FakeGateway]:
    registry = ProviderRegistry()
    entry = ProviderEntry(
        provider_id="codex",
        auth_service=FakeAuthService(),
        gateway=gateway,
        capabilities={"defaultModel": "gpt-5"},
    )
    registry.register(entry, default=True)
    return ChatService(registry=registry), gateway


class ChatServiceTests(unittest.TestCase):
    def test_chat_returns_output_text(self) -> None:
        gateway = FakeGateway()
        service, _ = _make_service(gateway)

        response = service.chat(
            {
                "messages": [{"role": "user", "content": "List the files"}],
            }
        )

        self.assertEqual(response["outputText"], "OK")
        self.assertEqual(response["provider"], "codex")

    def test_chat_forwards_provider_params(self) -> None:
        gateway = FakeGateway()
        service, gw = _make_service(gateway)

        service.chat(
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "providerParams": {"reasoningEffort": "high"},
            }
        )

        assert gw.last_kwargs is not None
        self.assertEqual(gw.last_kwargs["provider_params"], {"reasoningEffort": "high"})

    def test_messages_passed_through_unmodified(self) -> None:
        gateway = FakeGateway()
        service, gw = _make_service(gateway)

        service.chat(
            {
                "messages": [{"role": "user", "content": "Use a tool if needed"}],
            }
        )

        assert gw.last_kwargs is not None
        self.assertEqual(len(gw.last_kwargs["messages"]), 1)
        self.assertEqual(gw.last_kwargs["messages"][0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
