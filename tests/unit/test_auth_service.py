from __future__ import annotations

import unittest

from llm_broker.app.auth_service import AuthService
from llm_broker.domain.auth import AuthSession, CallbackPayload, OAuthLoginTicket


class FakeSessionStore:
    def __init__(self, session: AuthSession | None = None) -> None:
        self.session = session
        self.saved: list[AuthSession] = []
        self.cleared = False

    def load(self) -> AuthSession | None:
        return self.session

    def save(self, session: AuthSession) -> None:
        self.session = session
        self.saved.append(session)

    def clear(self) -> None:
        self.session = None
        self.cleared = True


class FakeCallbackServer:
    def __init__(self) -> None:
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True


class FakeCallbackServerFactory:
    def __init__(self) -> None:
        self.server = FakeCallbackServer()
        self.last_kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> FakeCallbackServer:
        self.last_kwargs = kwargs
        return self.server


class FakeOAuthGateway:
    def __init__(self) -> None:
        self.ticket = OAuthLoginTicket(
            id="login-1",
            state="state-1",
            verifier="verifier-1",
            challenge="challenge-1",
            redirect_uri="http://localhost:1455/auth/callback",
            auth_url="https://auth.openai.com/oauth/authorize?example=1",
            started_at=1000,
            expires_at=5000,
        )
        self.exchanged_with: str | None = None
        self.refreshed = False

    def create_login_ticket(self, *, now_ms: int, timeout_ms: int) -> OAuthLoginTicket:
        return OAuthLoginTicket(
            id=self.ticket.id,
            state=self.ticket.state,
            verifier=self.ticket.verifier,
            challenge=self.ticket.challenge,
            redirect_uri=self.ticket.redirect_uri,
            auth_url=self.ticket.auth_url,
            started_at=now_ms,
            expires_at=now_ms + timeout_ms,
        )

    def exchange_authorization_code(self, *, code: str, ticket: OAuthLoginTicket, now_ms: int) -> AuthSession:
        self.exchanged_with = code
        return AuthSession(
            provider="codex",
            access_token=f"access-{code}",
            refresh_token="refresh-1",
            expires_at=now_ms + 10_000,
            updated_at=now_ms,
            email="user@example.com",
        )

    def refresh_session(self, *, session: AuthSession, now_ms: int) -> AuthSession:
        self.refreshed = True
        return AuthSession(
            provider=session.provider,
            access_token="access-refreshed",
            refresh_token=session.refresh_token,
            expires_at=now_ms + 10_000,
            updated_at=now_ms,
            email=session.email,
        )


class AuthServiceTests(unittest.TestCase):
    def test_start_and_complete_login_use_ports(self) -> None:
        session_store = FakeSessionStore()
        oauth_gateway = FakeOAuthGateway()
        callback_factory = FakeCallbackServerFactory()
        auth_service = AuthService(
            session_store=session_store,
            oauth_gateway=oauth_gateway,
            callback_server_factory=callback_factory,
            login_timeout_ms=5_000,
            min_refresh_delay_ms=100,
            now=lambda: 1_000,
        )
        self.addCleanup(auth_service.logout)

        ticket = auth_service.start_login()
        self.assertEqual(ticket.id, "login-1")
        self.assertTrue(callback_factory.server.started)
        self.assertIsNotNone(callback_factory.last_kwargs)
        assert callback_factory.last_kwargs is not None
        self.assertEqual(callback_factory.last_kwargs["success_title"], "Authentication complete")
        self.assertIn("continue automatically", str(callback_factory.last_kwargs["success_message"]))

        auth_service.finish_login_from_callback(ticket.id, CallbackPayload(code="code-123", state=ticket.state))

        self.assertEqual(oauth_gateway.exchanged_with, "code-123")
        self.assertEqual(session_store.saved[-1].access_token, "access-code-123")
        self.assertIsNone(auth_service.get_state().active_login)

    def test_expired_session_triggers_refresh(self) -> None:
        session_store = FakeSessionStore(
            AuthSession(
                provider="codex",
                access_token="access-old",
                refresh_token="refresh-old",
                expires_at=900,
                updated_at=100,
            )
        )
        oauth_gateway = FakeOAuthGateway()
        auth_service = AuthService(
            session_store=session_store,
            oauth_gateway=oauth_gateway,
            callback_server_factory=FakeCallbackServerFactory(),
            login_timeout_ms=5_000,
            min_refresh_delay_ms=100,
            now=lambda: 1_000,
        )
        self.addCleanup(auth_service.logout)
        auth_service.initialize()

        session = auth_service.get_valid_session()

        self.assertTrue(oauth_gateway.refreshed)
        self.assertEqual(session.access_token, "access-refreshed")


if __name__ == "__main__":
    unittest.main()
