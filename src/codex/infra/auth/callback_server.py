from __future__ import annotations

import html
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, parse, request

from ...domain.auth import CallbackPayload
from ...domain.errors import BrokerError


def _build_html_response(title: str, message: str) -> str:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title}</title>
  <style>
    @font-face {{
      font-display: swap;
      font-family: "OpenAI Sans";
      font-style: normal;
      font-weight: 400;
      src: url("https://cdn.openai.com/common/fonts/openai-sans/v3/OpenAISans-Regular.woff2") format("woff2");
    }}
    @font-face {{
      font-display: swap;
      font-family: "OpenAI Sans";
      font-style: normal;
      font-weight: 500;
      src: url("https://cdn.openai.com/common/fonts/openai-sans/v3/OpenAISans-Medium.woff2") format("woff2");
    }}
    @font-face {{
      font-display: swap;
      font-family: "OpenAI Sans";
      font-style: normal;
      font-weight: 600;
      src: url("https://cdn.openai.com/common/fonts/openai-sans/v3/OpenAISans-Semibold.woff2") format("woff2");
    }}
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: rgba(255, 255, 255, 0.94);
      --ink: #171717;
      --muted: #5f5f5c;
      --border: rgba(23, 23, 23, 0.08);
      --shadow: rgba(16, 24, 40, 0.08);
      --accent: #10a37f;
      --accent-soft: rgba(16, 163, 127, 0.12);
      --button: #111111;
      --button-hover: #1a1a1a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 28px;
      background:
        radial-gradient(circle at top left, rgba(16, 163, 127, 0.10), transparent 28%),
        radial-gradient(circle at bottom right, rgba(16, 163, 127, 0.08), transparent 32%),
        linear-gradient(180deg, #fbfbf8 0%, #f4f4ef 100%);
      color: var(--ink);
      font-family: "OpenAI Sans", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .shell {{
      width: min(760px, 100%);
      padding: 28px;
      border-radius: 32px;
      border: 1px solid rgba(255, 255, 255, 0.8);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.84), rgba(255, 255, 255, 0.72));
      box-shadow: 0 24px 80px rgba(15, 23, 42, 0.08);
      backdrop-filter: blur(16px);
    }}
    .card {{
      width: 100%;
      padding: 28px 28px 24px;
      border-radius: 28px;
      border: 1px solid var(--border);
      background: var(--panel);
      box-shadow: 0 16px 48px var(--shadow);
    }}
    .badge {{
      width: 48px;
      height: 48px;
      display: inline-grid;
      place-items: center;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      box-shadow: inset 0 0 0 1px rgba(16, 163, 127, 0.12);
    }}
    .eyebrow {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 14px;
      font-weight: 500;
      letter-spacing: -0.01em;
    }}
    h1 {{
      margin: 10px 0 12px;
      font-size: clamp(32px, 6vw, 52px);
      line-height: 1;
      letter-spacing: -0.045em;
      font-weight: 600;
    }}
    p {{
      margin: 0;
      font-size: 16px;
      line-height: 1.65;
      color: var(--muted);
    }}
    .meta {{
      margin-top: 22px;
      padding: 14px 16px;
      border-radius: 18px;
      background: #f8f8f5;
      border: 1px solid rgba(23, 23, 23, 0.06);
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    .meta strong {{
      color: var(--ink);
      font-weight: 500;
    }}
    .actions {{
      margin-top: 24px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 13px 18px;
      background: var(--button);
      color: #ffffff;
      font: inherit;
      font-weight: 500;
      cursor: pointer;
      transition: background 120ms ease, transform 120ms ease;
    }}
    button:hover {{
      background: var(--button-hover);
      transform: translateY(-1px);
    }}
    .hint {{
      color: var(--muted);
      font-size: 14px;
    }}
    @media (max-width: 640px) {{
      .shell {{
        padding: 14px;
        border-radius: 24px;
      }}
      .card {{
        padding: 22px 20px 20px;
      }}
    }}
  </style>
  <script>
    window.addEventListener("load", function () {{
      setTimeout(function () {{
        try {{
          window.close();
        }} catch (error) {{
          return;
        }}
      }}, 700);
    }});
  </script>
</head>
<body>
  <main class="shell">
    <section class="card">
      <div class="badge" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none">
          <path d="M6.5 12.5l3.25 3.25L17.5 8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></path>
        </svg>
      </div>
      <div class="eyebrow">Signed in with ChatGPT</div>
      <h1>{safe_title}</h1>
      <p>{safe_message}</p>
      <div class="meta">
        <strong>What happens next?</strong><br />
        Codex-Bridge already received the authorization callback. If you started the login from the terminal, it should continue automatically without pressing Enter.
      </div>
      <div class="actions">
        <button type="button" onclick="window.close()">Close window</button>
        <span class="hint">You can safely return to your terminal or app.</span>
      </div>
    </section>
  </main>
</body>
</html>"""
def _request_cancellation(host: str, port: int, cancel_path: str) -> None:
    target = f"http://{host}:{port}{cancel_path}"
    try:
        request.urlopen(target, timeout=2).read()
    except Exception:
        return


class LocalCallbackServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        callback_path: str,
        cancel_path: str,
        expected_state: str,
        timeout_seconds: float,
        success_title: str,
        success_message: str,
        on_callback,
    ) -> None:
        self._host = host
        self._port = port
        self._callback_path = callback_path
        self._cancel_path = cancel_path
        self._expected_state = expected_state
        self._timeout_seconds = timeout_seconds
        self._success_title = success_title
        self._success_message = success_message
        self._on_callback = on_callback
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._timeout: threading.Timer | None = None

    def start(self) -> None:
        attempted_cancellation = False
        while True:
            try:
                self._listen_once()
                break
            except OSError as exc:
                if getattr(exc, "errno", None) == 98 and not attempted_cancellation:
                    attempted_cancellation = True
                    _request_cancellation(self._host, self._port, self._cancel_path)
                    time.sleep(0.2)
                    continue
                raise

        timeout = threading.Timer(self._timeout_seconds, self.close)
        timeout.daemon = True
        timeout.start()
        self._timeout = timeout

    def close(self) -> None:
        with self._lock:
            timeout = self._timeout
            self._timeout = None
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None

        if timeout:
            timeout.cancel()

        if server:
            server.shutdown()
            server.server_close()

        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _listen_once(self) -> None:
        server = ThreadingHTTPServer((self._host, self._port), self._create_handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with self._lock:
            self._server = server
            self._thread = thread

    def _create_handler(self) -> type[BaseHTTPRequestHandler]:
        callback_path = self._callback_path
        cancel_path = self._cancel_path
        expected_state = self._expected_state
        success_title = self._success_title
        success_message = self._success_message
        on_callback = self._on_callback
        owner = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = parse.urlparse(self.path)
                if parsed.path == cancel_path:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Cancellation requested.")
                    threading.Thread(target=owner.close, daemon=True).start()
                    return

                if parsed.path != callback_path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not found.")
                    return

                query = parse.parse_qs(parsed.query)
                code = (query.get("code", [""])[0] or "").strip()
                state = (query.get("state", [""])[0] or "").strip()

                if not code or not state or state != expected_state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid OAuth callback.")
                    return

                payload = CallbackPayload(code=code, state=state)
                body = _build_html_response(success_title, success_message).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

                threading.Thread(target=on_callback, args=(payload,), daemon=True).start()
                threading.Thread(target=owner.close, daemon=True).start()

        return CallbackHandler


@dataclass(frozen=True)
class LoopbackCallbackServerFactory:
    host: str
    port: int
    callback_path: str
    cancel_path: str
    timeout_seconds: float

    def create(
        self,
        *,
        expected_state: str,
        success_title: str,
        success_message: str,
        on_callback,
    ) -> LocalCallbackServer:
        return LocalCallbackServer(
            host=self.host,
            port=self.port,
            callback_path=self.callback_path,
            cancel_path=self.cancel_path,
            expected_state=expected_state,
            timeout_seconds=self.timeout_seconds,
            success_title=success_title,
            success_message=success_message,
            on_callback=on_callback,
        )
