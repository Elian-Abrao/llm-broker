from __future__ import annotations

import json
from typing import Any, Iterator
from urllib import error, request

from ....domain.auth import AuthSession
from ....domain.errors import BrokerError


DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

GEMINI_MODELS = [
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "description": "Most capable Gemini model.", "recommended": True},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "description": "Fast and efficient."},
    {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "description": "Previous generation Flash."},
]


def _collect_system_messages(messages: list[dict[str, Any]]) -> str | None:
    parts = [
        str(m.get("content", "")).strip()
        for m in messages
        if m.get("role") == "system" and str(m.get("content", "")).strip()
    ]
    return "\n\n".join(parts) if parts else None


def _build_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert broker message format to Gemini contents format."""
    contents: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", "")).strip()

        if role == "system":
            continue

        if role == "user":
            _flush_pending_tool_calls(contents, pending_tool_calls)
            contents.append({"role": "user", "parts": [{"text": str(msg.get("content", ""))}]})
            continue

        if role == "assistant":
            _flush_pending_tool_calls(contents, pending_tool_calls)
            contents.append({"role": "model", "parts": [{"text": str(msg.get("content", ""))}]})
            continue

        if role == "tool_call":
            args_raw = msg.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            pending_tool_calls.append({
                "call_id": str(msg.get("call_id", "")),
                "name": str(msg.get("name", "")),
                "args": args,
            })
            continue

        if role == "tool_result":
            if pending_tool_calls:
                model_parts = [
                    {"functionCall": {"id": tc["call_id"], "name": tc["name"], "args": tc["args"]}}
                    for tc in pending_tool_calls
                ]
                contents.append({"role": "model", "parts": model_parts})
                pending_tool_calls = []

            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "id": str(msg.get("call_id", "")),
                        "name": str(msg.get("name", "") or ""),
                        "response": {"output": str(msg.get("output", ""))},
                    }
                }],
            })
            continue

    _flush_pending_tool_calls(contents, pending_tool_calls)
    return contents


def _flush_pending_tool_calls(contents: list, pending: list) -> None:
    if pending:
        parts = [
            {"functionCall": {"id": tc["call_id"], "name": tc["name"], "args": tc["args"]}}
            for tc in pending
        ]
        contents.append({"role": "model", "parts": parts})
        pending.clear()


def _build_gemini_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Convert OpenAI-style tool definitions to Gemini functionDeclarations."""
    declarations = []
    for tool in tools:
        if tool.get("type") == "function":
            fn = tool.get("function", {})
            declarations.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            })
    if not declarations:
        return None
    return [{"functionDeclarations": declarations}]


def _iter_gemini_chunks(response) -> Iterator[dict[str, Any]]:
    """Parse Gemini streamGenerateContent response (JSON array stream)."""
    buffer = b""
    for chunk in response:
        buffer += chunk
    raw = buffer.decode("utf-8").strip()
    if raw.startswith("["):
        inner = raw[1:]
        if inner.endswith("]"):
            inner = inner[:-1]
        decoder = json.JSONDecoder()
        pos = 0
        while pos < len(inner):
            inner_stripped = inner[pos:].lstrip(" \t\r\n,")
            if not inner_stripped:
                break
            try:
                obj, idx = decoder.raw_decode(inner_stripped)
                pos += (len(inner) - len(inner_stripped)) + idx
                yield obj
            except json.JSONDecodeError:
                break
    else:
        for line in raw.splitlines():
            line = line.strip().lstrip(",")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


class GeminiCliHttpGateway:
    def __init__(self, *, base_url: str = DEFAULT_GEMINI_BASE_URL, user_agent: str = "llm-broker/python") -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent

    def stream_chat(
        self,
        *,
        request_id: str,
        session: AuthSession,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider_params: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        model = model or DEFAULT_GEMINI_MODEL

        system_text = _collect_system_messages(messages)
        contents = _build_gemini_contents(messages)

        body: dict[str, Any] = {"contents": contents}
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        gemini_tools = _build_gemini_tools(tools) if tools else None
        if gemini_tools:
            body["tools"] = gemini_tools

        if provider_params:
            gen_config = {}
            if "maxOutputTokens" in provider_params:
                gen_config["maxOutputTokens"] = provider_params["maxOutputTokens"]
            if "temperature" in provider_params:
                gen_config["temperature"] = provider_params["temperature"]
            if gen_config:
                body["generationConfig"] = gen_config

        url = f"{self._base_url}/v1beta/models/{model}:streamGenerateContent"
        headers = {
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }

        req = request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            response = request.urlopen(req, timeout=120)
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            raise BrokerError(502, f"Gemini request failed ({exc.code}): {raw_body}", raw_body) from exc
        except error.URLError as exc:
            raise BrokerError(502, str(exc.reason)) from exc

        with response:
            for chunk in _iter_gemini_chunks(response):
                candidates = chunk.get("candidates")
                if not isinstance(candidates, list) or not candidates:
                    continue

                candidate = candidates[0]
                content = candidate.get("content") or {}
                parts = content.get("parts") or []
                finish_reason = candidate.get("finishReason")

                for part in parts:
                    if "text" in part:
                        text = str(part["text"])
                        if text:
                            yield {"requestId": request_id, "provider": "gemini_cli", "kind": "delta", "delta": text}

                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        call_id = str(fc.get("id") or fc.get("name") or "")
                        name = str(fc.get("name", ""))
                        args = fc.get("args", {})
                        args_str = json.dumps(args) if isinstance(args, dict) else str(args)

                        yield {"requestId": request_id, "provider": "gemini_cli", "kind": "tool_call_start", "callId": call_id, "name": name}
                        yield {"requestId": request_id, "provider": "gemini_cli", "kind": "tool_call_delta", "callId": call_id, "delta": args_str}
                        yield {"requestId": request_id, "provider": "gemini_cli", "kind": "tool_call_done", "callId": call_id, "name": name, "arguments": args_str}

                if finish_reason and finish_reason not in {"STOP", "MAX_TOKENS", "FINISH_REASON_UNSPECIFIED", ""}:
                    if finish_reason in {"SAFETY", "RECITATION"}:
                        yield {"requestId": request_id, "provider": "gemini_cli", "kind": "error", "message": f"Gemini stopped: {finish_reason}"}
                        return

        yield {"requestId": request_id, "provider": "gemini_cli", "kind": "done"}
