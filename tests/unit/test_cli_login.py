from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from codex.domain.auth import OAuthLoginTicket
from codex.interfaces import cli


class _FakeState:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return self._payload


class _FakeAuthService:
    def __init__(self) -> None:
        self._state_calls = 0

    def get_state(self) -> _FakeState:
        self._state_calls += 1
        if self._state_calls == 1:
            return _FakeState({"isRefreshing": False})
        return _FakeState(
            {
                "isRefreshing": False,
                "session": {
                    "provider": "codex",
                    "email": "user@example.com",
                    "planType": "plus",
                    "accountId": "acct_123",
                    "expiresAt": 10_000,
                    "updatedAt": 5_000,
                },
            }
        )

    def start_login(self) -> OAuthLoginTicket:
        return OAuthLoginTicket(
            id="login-1",
            state="state-1",
            verifier="verifier-1",
            challenge="challenge-1",
            redirect_uri="http://localhost:1455/auth/callback",
            auth_url="https://auth.openai.com/oauth/authorize?example=1",
            started_at=1_000,
            expires_at=60_000,
        )


class _FakeRuntime:
    def __init__(self) -> None:
        self.auth_service = _FakeAuthService()


class LoginCliTests(unittest.TestCase):
    def test_login_waits_for_callback_automatically(self) -> None:
        output = io.StringIO()

        with (
            patch("codex.interfaces.cli.create_runtime", return_value=_FakeRuntime()),
            patch("codex.interfaces.cli.webbrowser.open", return_value=True),
            patch("codex.interfaces.cli.sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=AssertionError("input should not be required for automatic callback")),
            redirect_stdout(output),
        ):
            cli._run_login(as_json=False, open_browser=True)

        text = output.getvalue()
        self.assertIn("Waiting for the browser callback.", text)
        self.assertIn("Login completed.", text)
        self.assertIn("user@example.com", text)


if __name__ == "__main__":
    unittest.main()
