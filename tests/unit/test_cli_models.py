from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from llm_broker.interfaces import cli


class _FakeChatService:
    def __init__(self) -> None:
        self.requested_provider: str | None = None

    def get_capabilities(self, provider_id: str | None = None) -> dict[str, object]:
        self.requested_provider = provider_id
        if provider_id == "gemini_cli":
            return {
                "provider": "gemini_cli",
                "billingMode": "usage",
                "authenticated": True,
                "defaultModel": "gemini-2.5-flash",
                "models": [
                    {"id": "gemini-2.5-flash", "recommended": True},
                    {"id": "gemini-2.5-pro"},
                ],
            }
        return {
            "provider": "codex",
            "billingMode": "monthly",
            "authenticated": True,
            "defaultModel": "gpt-5.4",
            "models": [
                {"id": "gpt-5.4", "recommended": True},
                {"id": "gpt-5-mini"},
            ],
            "reasoningEfforts": [
                {"id": "medium", "recommended": True},
            ],
        }


class _FakeRuntime:
    def __init__(self) -> None:
        self.chat_service = _FakeChatService()


class ModelsCliTests(unittest.TestCase):
    def test_models_uses_requested_provider(self) -> None:
        runtime = _FakeRuntime()
        output = io.StringIO()

        with (
            patch("llm_broker.interfaces.cli.create_runtime", return_value=runtime),
            redirect_stdout(output),
        ):
            cli._run_models(as_json=False, provider="gemini_cli")

        text = output.getvalue()
        self.assertEqual(runtime.chat_service.requested_provider, "gemini_cli")
        self.assertIn("Provider: gemini_cli", text)
        self.assertIn("Default model: gemini-2.5-flash", text)
        self.assertIn("gemini-2.5-flash (recommended)", text)
        self.assertNotIn("gpt-5.4", text)


if __name__ == "__main__":
    unittest.main()
