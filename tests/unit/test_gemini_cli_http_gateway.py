from __future__ import annotations

import io
import json
import unittest
from urllib import error
from unittest.mock import patch

from llm_broker.domain.auth import AuthSession
from llm_broker.domain.errors import BrokerError
from llm_broker.infra.providers.gemini_cli.http_gateway import GeminiCliHttpGateway


class _FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class GeminiCliHttpGatewayTests(unittest.TestCase):
    def test_stream_chat_uses_code_assist_endpoint_and_payload(self) -> None:
        captured: dict[str, object] = {}

        def _fake_urlopen(req, timeout=120):  # type: ignore[no-untyped-def]
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse(
                [
                    b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"hello"}]}}]}}\n',
                    b"\n",
                ]
            )

        gateway = GeminiCliHttpGateway(base_url="https://cloudcode-pa.googleapis.com")
        session = AuthSession(
            provider="gemini_cli",
            access_token="token-123",
            refresh_token="refresh-123",
            expires_at=10_000,
            updated_at=5_000,
        )

        with (
            patch("llm_broker.infra.providers.gemini_cli.http_gateway.request.urlopen", side_effect=_fake_urlopen),
            patch.dict(
                "llm_broker.infra.providers.gemini_cli.http_gateway.os.environ",
                {"GOOGLE_CLOUD_PROJECT": "demo-project"},
                clear=False,
            ),
        ):
            events = list(
                gateway.stream_chat(
                    request_id="req-1",
                    session=session,
                    model="gemini-2.5-pro",
                    messages=[{"role": "user", "content": "hi"}],
                    tools=None,
                    provider_params={"temperature": 0.2, "maxOutputTokens": 256},
                )
            )

        self.assertEqual(
            captured["url"],
            "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
        )
        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "gemini-2.5-pro")
        self.assertEqual(body["project"], "demo-project")
        self.assertEqual(body["user_prompt_id"], "req-1")
        self.assertNotIn("enabled_credit_types", body)
        self.assertEqual(body["request"]["contents"], [{"role": "user", "parts": [{"text": "hi"}]}])
        self.assertEqual(body["request"]["generationConfig"]["temperature"], 0.2)
        self.assertEqual(body["request"]["generationConfig"]["maxOutputTokens"], 256)

        headers = captured["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer token-123")
        self.assertEqual(headers["Content-type"], "application/json")
        self.assertEqual(headers["Accept"], "text/event-stream")
        self.assertEqual(events[0]["kind"], "delta")
        self.assertEqual(events[0]["delta"], "hello")
        self.assertEqual(events[-1]["kind"], "done")

    def test_stream_chat_surfaces_scope_error_with_relogin_hint(self) -> None:
        gateway = GeminiCliHttpGateway(base_url="https://cloudcode-pa.googleapis.com")
        session = AuthSession(
            provider="gemini_cli",
            access_token="token-123",
            refresh_token="refresh-123",
            expires_at=10_000,
            updated_at=5_000,
        )

        with patch(
            "llm_broker.infra.providers.gemini_cli.http_gateway.request.urlopen",
            side_effect=error.HTTPError(
                url="https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"error":{"details":[{"reason":"ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}'
                ),
            ),
        ):
            with self.assertRaises(BrokerError) as ctx:
                list(
                    gateway.stream_chat(
                        request_id="req-1",
                        session=session,
                        model="gemini-2.5-pro",
                        messages=[{"role": "user", "content": "hi"}],
                    )
                )

        self.assertIn("Run login again", str(ctx.exception))

    def test_stream_chat_normalizes_legacy_g1_credit_type(self) -> None:
        captured: dict[str, object] = {}

        def _fake_urlopen(req, timeout=120):  # type: ignore[no-untyped-def]
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse([b"\n"])

        gateway = GeminiCliHttpGateway(base_url="https://cloudcode-pa.googleapis.com")
        session = AuthSession(
            provider="gemini_cli",
            access_token="token-123",
            refresh_token="refresh-123",
            expires_at=10_000,
            updated_at=5_000,
        )

        with patch("llm_broker.infra.providers.gemini_cli.http_gateway.request.urlopen", side_effect=_fake_urlopen):
            events = list(
                gateway.stream_chat(
                    request_id="req-1",
                    session=session,
                    model="gemini-2.5-pro",
                    messages=[{"role": "user", "content": "hi"}],
                    provider_params={"enabledCreditTypes": ["G1"]},
                )
            )

        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["enabled_credit_types"], ["GOOGLE_ONE_AI"])
        self.assertEqual(events[-1]["kind"], "done")


if __name__ == "__main__":
    unittest.main()
