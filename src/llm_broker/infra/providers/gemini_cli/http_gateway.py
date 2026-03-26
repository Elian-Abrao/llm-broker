from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Iterator
from urllib import error, request

from ....domain.auth import AuthSession
from ....domain.errors import BrokerError


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_BASE_URL = "https://cloudcode-pa.googleapis.com"
DEFAULT_GEMINI_API_VERSION = "v1internal"

GEMINI_MODELS = [
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "description": "Fast and efficient.", "recommended": True},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "description": "Most capable Gemini model."},
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


def _iter_sse_chunks(response) -> Iterator[dict[str, Any]]:
    """Parse Code Assist SSE responses composed of `data: ...` frames."""
    buffered_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line.startswith("data: "):
            buffered_lines.append(line[6:].strip())
            continue
        if line:
            continue
        if not buffered_lines:
            continue
        payload = "\n".join(buffered_lines)
        buffered_lines.clear()
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue

    if buffered_lines:
        try:
            yield json.loads("\n".join(buffered_lines))
        except json.JSONDecodeError:
            return


def _build_generation_config(provider_params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not provider_params:
        return None
    generation_config: dict[str, Any] = {}
    if "maxOutputTokens" in provider_params:
        generation_config["maxOutputTokens"] = provider_params["maxOutputTokens"]
    if "temperature" in provider_params:
        generation_config["temperature"] = provider_params["temperature"]
    return generation_config or None


def _resolve_project_id(provider_params: dict[str, Any] | None) -> str | None:
    project_id = None
    if provider_params:
        raw_project = provider_params.get("project")
        if isinstance(raw_project, str) and raw_project.strip():
            project_id = raw_project.strip()
    if project_id:
        return project_id
    return os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT_ID") or None


def _build_load_code_assist_request(project_id: str | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "ideType": "IDE_UNSPECIFIED",
        "platform": "PLATFORM_UNSPECIFIED",
        "pluginType": "GEMINI",
    }
    if project_id:
        metadata["duetProject"] = project_id
    return {
        "cloudaicompanionProject": project_id,
        "metadata": metadata,
    }


def _normalize_enabled_credit_types(provider_params: dict[str, Any] | None) -> list[str] | None:
    if not provider_params:
        return None
    raw_credit_types = provider_params.get("enabledCreditTypes")
    if not isinstance(raw_credit_types, list):
        return None
    normalized: list[str] = []
    for value in raw_credit_types:
        if not isinstance(value, str):
            continue
        credit_type = value.strip()
        if not credit_type:
            continue
        if credit_type == "G1":
            credit_type = "GOOGLE_ONE_AI"
        normalized.append(credit_type)
    return normalized or None


def _extract_retry_delay_seconds(raw_body: str) -> float | None:
    match = re.search(r"reset after (\d+)s", raw_body, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1)) + 1.0
    except ValueError:
        return None


def _build_code_assist_request(
    *,
    request_id: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    provider_params: dict[str, Any] | None,
) -> dict[str, Any]:
    system_text = _collect_system_messages(messages)
    contents = _build_gemini_contents(messages)
    req: dict[str, Any] = {
        "contents": contents,
    }
    if system_text:
        req["systemInstruction"] = {"role": "user", "parts": [{"text": system_text}]}
    gemini_tools = _build_gemini_tools(tools) if tools else None
    if gemini_tools:
        req["tools"] = gemini_tools
    generation_config = _build_generation_config(provider_params)
    if generation_config:
        req["generationConfig"] = generation_config
    session_id = provider_params.get("sessionId") if provider_params else None
    if isinstance(session_id, str) and session_id.strip():
        req["session_id"] = session_id.strip()

    body: dict[str, Any] = {
        "model": model,
        "user_prompt_id": request_id,
        "request": req,
    }
    project_id = _resolve_project_id(provider_params)
    if project_id:
        body["project"] = project_id
    enabled_credit_types = _normalize_enabled_credit_types(provider_params)
    if enabled_credit_types:
        body["enabled_credit_types"] = enabled_credit_types
    return body


class GeminiCliHttpGateway:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        api_version: str = DEFAULT_GEMINI_API_VERSION,
        user_agent: str = "llm-broker/python",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version.strip("/") or DEFAULT_GEMINI_API_VERSION
        self._user_agent = user_agent

    def _request_json(self, *, session: AuthSession, url: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        last_error: BrokerError | None = None
        for _ in range(2):
            req = request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with request.urlopen(req, timeout=120) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except error.HTTPError as exc:
                raw_body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429:
                    delay = _extract_retry_delay_seconds(raw_body)
                    if delay is not None:
                        time.sleep(delay)
                        continue
                last_error = BrokerError(502, f"Gemini request failed ({exc.code}): {raw_body}", raw_body)
                break
            except error.URLError as exc:
                last_error = BrokerError(502, str(exc.reason))
                break
        if last_error is not None:
            raise last_error
        raise BrokerError(502, "Gemini request failed for an unknown reason.")

    def _yield_response_events(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return

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

    def _generate_content_fallback(
        self,
        *,
        request_id: str,
        session: AuthSession,
        body: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        payload = self._request_json(
            session=session,
            url=f"{self._base_url}/{self._api_version}:generateContent",
            body=body,
        )
        response = payload.get("response")
        if isinstance(response, dict):
            yield from self._yield_response_events(request_id=request_id, payload=response)
        yield {"requestId": request_id, "provider": "gemini_cli", "kind": "done"}

    def _resolve_code_assist_project(
        self,
        *,
        session: AuthSession,
        provider_params: dict[str, Any] | None,
    ) -> str | None:
        explicit_project = _resolve_project_id(provider_params)
        url = f"{self._base_url}/{self._api_version}:loadCodeAssist"
        payload = self._request_json(
            session=session,
            url=url,
            body=_build_load_code_assist_request(explicit_project),
        )
        project_id = payload.get("cloudaicompanionProject")
        if isinstance(project_id, str) and project_id.strip():
            return project_id.strip()
        if explicit_project:
            return explicit_project
        return None

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
        resolved_provider_params = dict(provider_params or {})
        resolved_project_id = self._resolve_code_assist_project(
            session=session,
            provider_params=resolved_provider_params,
        )
        if resolved_project_id:
            resolved_provider_params["project"] = resolved_project_id
        body = _build_code_assist_request(
            request_id=request_id,
            model=model,
            messages=messages,
            tools=tools,
            provider_params=resolved_provider_params,
        )
        url = f"{self._base_url}/{self._api_version}:streamGenerateContent?alt=sse"
        headers = {
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": self._user_agent,
        }

        response = None
        last_error: BrokerError | None = None
        for _ in range(2):
            req = request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                response = request.urlopen(req, timeout=120)
                break
            except error.HTTPError as exc:
                raw_body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 404:
                    yield from self._generate_content_fallback(
                        request_id=request_id,
                        session=session,
                        body=body,
                    )
                    return
                if exc.code == 429:
                    delay = _extract_retry_delay_seconds(raw_body)
                    if delay is not None:
                        time.sleep(delay)
                        continue
                if exc.code == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in raw_body:
                    raise BrokerError(
                        502,
                        "Gemini Code Assist rejected the token because it lacks the required scopes. "
                        "Run login again for the Gemini provider to mint a fresh token with the Code Assist scopes.",
                        raw_body,
                    ) from exc
                last_error = BrokerError(502, f"Gemini request failed ({exc.code}): {raw_body}", raw_body)
                break
            except error.URLError as exc:
                last_error = BrokerError(502, str(exc.reason))
                break
        if response is None:
            if last_error is not None:
                raise last_error
            raise BrokerError(502, "Gemini request failed for an unknown reason.")

        with response:
            for chunk in _iter_sse_chunks(response):
                payload = chunk.get("response") or {}
                yield from self._yield_response_events(request_id=request_id, payload=payload)

        yield {"requestId": request_id, "provider": "gemini_cli", "kind": "done"}
