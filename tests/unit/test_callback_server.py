from __future__ import annotations

import unittest

from llm_broker.infra.auth.callback_server import _build_html_response


class CallbackServerHtmlTests(unittest.TestCase):
    def test_success_page_mentions_automatic_continuation(self) -> None:
        html = _build_html_response(
            "Authentication complete",
            "Your session is ready. You can close this window and return to the terminal or app. It should continue automatically.",
        )

        self.assertIn("OpenAI Sans", html)
        self.assertIn("Authentication complete", html)
        self.assertIn("continue automatically", html)
        self.assertNotIn("Signed in with ChatGPT", html)
        self.assertNotIn("Codex-Bridge already received", html)
        self.assertIn("window.close()", html)


if __name__ == "__main__":
    unittest.main()
