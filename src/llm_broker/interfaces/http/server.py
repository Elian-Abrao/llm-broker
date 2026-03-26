from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ...bootstrap.config import DEFAULT_BIND_HOST, DEFAULT_BIND_PORT
from ...bootstrap.runtime import BrokerRuntime, create_runtime
from ...domain.errors import BrokerError
from .api import handle_json_request, parse_json_body


def _format_sse_event(event: dict[str, object]) -> bytes:
    return f"event: {event['kind']}\ndata: {json.dumps(event)}\n\n".encode("utf-8")


def create_handler(runtime: BrokerRuntime) -> type[BaseHTTPRequestHandler]:
    class BrokerHandler(BaseHTTPRequestHandler):
        server_version = "codex-bridge/1.0.0"

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length) if content_length > 0 else None
            try:
                if self.command == "POST" and self._is_stream_route():
                    self._handle_stream(body)
                    return

                status, payload = handle_json_request(runtime, self.command, self.path, body)
                self._write_json(status, payload)
            except BrokerError as exc:
                self._write_json(exc.status_code, json.dumps({"error": str(exc)}).encode("utf-8"))
            except Exception as exc:
                self._write_json(500, json.dumps({"error": str(exc)}).encode("utf-8"))

        def _is_stream_route(self) -> bool:
            return urlparse(self.path).path in {"/chat/stream", "/v1/chat/stream"}

        def _handle_stream(self, body: bytes | None) -> None:
            payload = parse_json_body(body)
            stream = runtime.chat_service.stream_chat(payload)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            try:
                for event in stream:
                    self.wfile.write(_format_sse_event(event))
                    self.wfile.flush()
            except BrokerError as exc:
                error_event = {
                    "requestId": "broker",
                    "provider": "codex",
                    "kind": "error",
                    "message": str(exc),
                }
                self.wfile.write(_format_sse_event(error_event))
                self.wfile.flush()

        def _write_json(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return BrokerHandler


def run_server(
    host: str = DEFAULT_BIND_HOST,
    port: int = DEFAULT_BIND_PORT,
    runtime: BrokerRuntime | None = None,
) -> None:
    active_runtime = runtime or create_runtime()
    server = ThreadingHTTPServer((host, port), create_handler(active_runtime))
    print(f"codex-bridge listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
