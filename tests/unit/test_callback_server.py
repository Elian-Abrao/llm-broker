from __future__ import annotations

import unittest

from llm_broker.infra.auth.callback_server import _build_html_response


class CallbackServerHtmlTests(unittest.TestCase):
    def test_success_page_mentions_automatic_continuation(self) -> None:
        html = _build_html_response(
            "Codex-Bridge connected",
            "Your Codex session is ready. You can close this window and return to Codex-Bridge. The terminal or app will continue automatically.",
        )

        self.assertIn("OpenAI Sans", html)
        self.assertIn("Codex-Bridge connected", html)
        self.assertIn("continue automatically", html)
        self.assertIn("window.close()", html)


if __name__ == "__main__":
    unittest.main()
