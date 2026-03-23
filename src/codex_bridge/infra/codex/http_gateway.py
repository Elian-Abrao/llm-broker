from __future__ import annotations

import json
from typing import Any, Iterator
from urllib import error, request

from ...bootstrap.config import (
    CODEX_ORIGINATOR,
)
from ...domain.auth import AuthSession
from ...domain.errors import BrokerError
from .default_instructions import CODEX_DEFAULT_INSTRUCTIONS


def _to_json_record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _collect_system_messages(messages: list[dict[str, Any]]) -> str | None:
    collected = [
        str(message.get("content", "")).strip()
        for message in messages
        if message.get("role") == "system" and str(message.get("content", "")).strip()
    ]
    return "\n\n".join(collected) if collected else None


def _build_instructions(messages: list[dict[str, Any]]) -> str:
    system_instructions = _collect_system_messages(messages)
    if not system_instructions:
        return CODEX_DEFAULT_INSTRUCTIONS
    return f"{CODEX_DEFAULT_INSTRUCTIONS}\n\n## User-defined instructions\n\n{system_instructions}"


def _build_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "")).strip()
        if role == "system":
            continue
        if role == "tool_call":
            payload.append({
                "type": "function_call",
                "name": message.get("name", ""),
                "call_id": message.get("call_id", ""),
                "arguments": message.get("arguments", ""),
            })
            continue
        if role == "tool_result":
            payload.append({
                "type": "function_call_output",
                "call_id": message.get("call_id", ""),
                "output": str(message.get("output", "")),
            })
            continue
        content = str(message.get("content", ""))
        payload.append(
            {
                "role": role,
                "content": [
                    {
                        "type": "output_text" if role == "assistant" else "input_text",
                        "text": content,
                    }
                ],
            }
        )
    return payload


def _iter_sse_events(response) -> Iterator[dict[str, str]]:
    event_name: str | None = None
    data_parts: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data_parts:
                yield {"event": event_name or "", "data": "\n".join(data_parts)}
            event_name = None
            data_parts = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_parts.append(line[len("data:") :].lstrip())
    if data_parts:
        yield {"event": event_name or "", "data": "\n".join(data_parts)}


def _normalize_codex_base_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if (
        (trimmed.startswith("https://chatgpt.com") or trimmed.startswith("https://chat.openai.com"))
        and "/backend-api/codex" not in trimmed
    ):
        return f"{trimmed}/backend-api/codex"
    return trimmed


class CodexHttpGateway:
    def __init__(self, *, base_url: str, user_agent: str) -> None:
        self._base_url = _normalize_codex_base_url(base_url)
        self._user_agent = user_agent

    def stream_chat(
        self,
        *,
        request_id: str,
        session: AuthSession,
        model: str,
        reasoning_effort: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "originator": CODEX_ORIGINATOR,
            "User-Agent": self._user_agent,
        }
        if session.account_id:
            headers["ChatGPT-Account-Id"] = session.account_id

        body: dict[str, Any] = {
            "model": model,
            "stream": True,
            "store": False,
            "instructions": _build_instructions(messages),
            "input": _build_input(messages),
        }
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        if tools:
            body["tools"] = tools

        req = request.Request(
            f"{self._base_url}/responses",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            response = request.urlopen(req, timeout=120)
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            raise BrokerError(502, f"Request failed ({exc.code}): {raw_body}", raw_body) from exc
        except error.URLError as exc:
            raise BrokerError(502, str(exc.reason)) from exc

        # Track function call metadata across SSE events
        _fc_meta: dict[str, dict[str, str]] = {}  # item_id → {"call_id", "name"}

        with response:
            for envelope in _iter_sse_events(response):
                data = envelope.get("data", "")
                if data == "[DONE]":
                    yield {"requestId": request_id, "provider": "codex", "kind": "done"}
                    return

                payload = _to_json_record(json.loads(data))
                if not payload:
                    continue

                event_type = payload.get("type")

                if event_type == "response.output_text.delta" and isinstance(payload.get("delta"), str):
                    yield {
                        "requestId": request_id,
                        "provider": "codex",
                        "kind": "delta",
                        "delta": payload["delta"],
                    }
                    continue

                if event_type == "response.output_item.added":
                    item = _to_json_record(payload.get("item")) or {}
                    if item.get("type") == "function_call":
                        item_id = str(item.get("id", ""))
                        call_id = str(item.get("call_id") or item.get("id", ""))
                        name = str(item.get("name", ""))
                        _fc_meta[item_id] = {"call_id": call_id, "name": name}
                        yield {
                            "requestId": request_id,
                            "provider": "codex",
                            "kind": "tool_call_start",
                            "callId": call_id,
                            "name": name,
                        }
                    continue

                if event_type == "response.function_call_arguments.delta":
                    item_id = str(payload.get("item_id", ""))
                    meta = _fc_meta.get(item_id, {})
                    yield {
                        "requestId": request_id,
                        "provider": "codex",
                        "kind": "tool_call_delta",
                        "callId": meta.get("call_id", item_id),
                        "delta": payload.get("delta", ""),
                    }
                    continue

                if event_type == "response.function_call_arguments.done":
                    item_id = str(payload.get("item_id", ""))
                    meta = _fc_meta.get(item_id, {})
                    yield {
                        "requestId": request_id,
                        "provider": "codex",
                        "kind": "tool_call_done",
                        "callId": meta.get("call_id", item_id),
                        "name": meta.get("name", ""),
                        "arguments": payload.get("arguments", ""),
                    }
                    continue

                if event_type == "response.failed":
                    error_payload = _to_json_record(payload.get("error")) or {}
                    message = error_payload.get("message")
                    yield {
                        "requestId": request_id,
                        "provider": "codex",
                        "kind": "error",
                        "message": message if isinstance(message, str) and message else "Codex returned a failed response event.",
                    }
                    return

                if event_type == "response.completed":
                    yield {"requestId": request_id, "provider": "codex", "kind": "done"}
                    return
