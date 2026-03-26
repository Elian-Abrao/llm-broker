from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse, parse_qs

from ...bootstrap.config import BRIDGE_API_PREFIX, BRIDGE_SERVICE_NAME
from ...bootstrap.runtime import BrokerRuntime
from ...domain.errors import BrokerError


JsonDict = dict[str, Any]


def _json_response(status: HTTPStatus, payload: JsonDict) -> tuple[int, bytes]:
    return int(status), json.dumps(payload).encode("utf-8")


def _normalize_path(path: str) -> str:
    return urlparse(path).path or "/"


def _is_route(path: str, route_path: str) -> bool:
    normalized = _normalize_path(path)
    return normalized == route_path or normalized == f"{BRIDGE_API_PREFIX}{route_path}"


def _split_api_path(path: str) -> list[str]:
    normalized = _normalize_path(path)
    if normalized.startswith(f"{BRIDGE_API_PREFIX}/"):
        normalized = normalized[len(BRIDGE_API_PREFIX) :]
    parts = [part for part in normalized.split("/") if part]
    return parts


def parse_json_body(body: bytes | None) -> JsonDict:
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BrokerError(400, "Malformed JSON request body.") from exc
    if not isinstance(parsed, dict):
        raise BrokerError(400, "JSON request body must be an object.")
    return parsed


def handle_json_request(runtime: BrokerRuntime, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    if method == "GET" and _is_route(path, "/health"):
        return _json_response(
            HTTPStatus.OK,
            {
                "ok": True,
                "service": BRIDGE_SERVICE_NAME,
            },
        )

    if method == "GET" and _is_route(path, "/auth/state"):
        query = parse_qs(urlparse(path).query)
        provider_param = query.get("provider", [None])[0]
        if provider_param:
            entry = runtime.registry.get(provider_param)
            return _json_response(HTTPStatus.OK, entry.auth_service.get_state().to_dict())
        return _json_response(HTTPStatus.OK, runtime.auth_service.get_state().to_dict())

    if method == "GET" and _is_route(path, "/providers"):
        return _json_response(HTTPStatus.OK, runtime.chat_service.get_all_capabilities())

    if method == "GET" and _is_route(path, "/providers/codex/options"):
        return _json_response(HTTPStatus.OK, runtime.chat_service.get_capabilities("codex"))

    if method == "POST" and _is_route(path, "/auth/login"):
        payload = parse_json_body(body)
        provider_id = str(payload.get("provider") or "codex").strip()
        entry = runtime.registry.get(provider_id)
        login = entry.auth_service.start_login()
        resp_payload = {
            **login.to_dict(include_started_at=False),
            "instructions": [
                "Open authUrl in your browser.",
                "If the automatic callback fails, send the final redirect URL to POST /v1/auth/complete.",
            ],
        }
        return _json_response(HTTPStatus.OK, resp_payload)

    if method == "POST" and _is_route(path, "/auth/complete"):
        payload = parse_json_body(body)
        redirect_url = payload.get("redirectUrl")
        if not isinstance(redirect_url, str) or not redirect_url.strip():
            raise BrokerError(400, "`redirectUrl` is required.")
        provider_id = str(payload.get("provider") or "codex").strip()
        entry = runtime.registry.get(provider_id)
        entry.auth_service.complete_manual_login(redirect_url)
        return _json_response(HTTPStatus.OK, entry.auth_service.get_state().to_dict())

    if method == "POST" and _is_route(path, "/auth/logout"):
        payload = parse_json_body(body)
        provider_id = str(payload.get("provider") or "codex").strip()
        entry = runtime.registry.get(provider_id)
        entry.auth_service.logout()
        return _json_response(HTTPStatus.OK, {"ok": True})

    if method == "POST" and _is_route(path, "/chat"):
        payload = parse_json_body(body)
        response = runtime.chat_service.chat(payload)
        return _json_response(HTTPStatus.OK, response)

    if method == "POST" and _is_route(path, "/chat/stream"):
        raise BrokerError(500, "Streaming route must be handled separately.")

    parts = _split_api_path(path)

    # GET /v1/providers/{provider_id}/options
    if len(parts) == 3 and parts[0] == "providers" and parts[2] == "options" and method == "GET":
        provider_id = parts[1]
        return _json_response(HTTPStatus.OK, runtime.chat_service.get_capabilities(provider_id))

    if parts[:2] == ["agent", "tools"] and method == "GET":
        return _json_response(HTTPStatus.OK, {"tools": runtime.agent_service.list_tools()})

    if parts[:2] == ["agent", "sessions"] and len(parts) == 2 and method == "POST":
        payload = parse_json_body(body)
        session = runtime.agent_service.create_session(
            mode=str(payload.get("mode") or "agent"),
            model=payload.get("model") if isinstance(payload.get("model"), str) else None,
            reasoning_effort=payload.get("reasoningEffort") if isinstance(payload.get("reasoningEffort"), str) else None,
            permission_profile=payload.get("permissionProfile") if isinstance(payload.get("permissionProfile"), str) else None,
            approval_policy=payload.get("approvalPolicy") if isinstance(payload.get("approvalPolicy"), str) else None,
            cwd=payload.get("cwd") if isinstance(payload.get("cwd"), str) else None,
        )
        return _json_response(HTTPStatus.OK, {"session": session.to_dict()})

    if parts[:2] == ["agent", "sessions"] and len(parts) >= 3:
        session_id = parts[2]

        if len(parts) == 3 and method == "GET":
            return _json_response(HTTPStatus.OK, {"session": runtime.agent_service.get_session_snapshot(session_id)})

        if len(parts) == 4 and parts[3] == "reset" and method == "POST":
            session = runtime.agent_service.reset_session(session_id)
            return _json_response(HTTPStatus.OK, {"session": session.to_dict()})

        if len(parts) == 4 and parts[3] == "permissions" and method == "POST":
            payload = parse_json_body(body)
            permission_profile = payload.get("permissionProfile")
            if not isinstance(permission_profile, str) or not permission_profile.strip():
                raise BrokerError(400, "`permissionProfile` is required.")
            session = runtime.agent_service.set_permissions(session_id, permission_profile)
            return _json_response(HTTPStatus.OK, {"session": session.to_dict()})

        if len(parts) == 4 and parts[3] == "approval-policy" and method == "POST":
            payload = parse_json_body(body)
            approval_policy = payload.get("approvalPolicy")
            if not isinstance(approval_policy, str) or not approval_policy.strip():
                raise BrokerError(400, "`approvalPolicy` is required.")
            session = runtime.agent_service.set_approval_policy(session_id, approval_policy)
            return _json_response(HTTPStatus.OK, {"session": session.to_dict()})

        if len(parts) == 4 and parts[3] == "turns" and method == "POST":
            payload = parse_json_body(body)
            prompt = payload.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise BrokerError(400, "`prompt` is required.")
            events = list(runtime.agent_service.send_turn(session_id, prompt))
            session = runtime.agent_service.get_session_snapshot(session_id)
            return _json_response(HTTPStatus.OK, {"session": session, "events": events})

        if len(parts) == 6 and parts[3] == "actions" and parts[5] == "approve" and method == "POST":
            action_id = parts[4]
            events = list(runtime.agent_service.approve_action(session_id, action_id))
            session = runtime.agent_service.get_session_snapshot(session_id)
            return _json_response(HTTPStatus.OK, {"session": session, "events": events})

        if len(parts) == 6 and parts[3] == "actions" and parts[5] == "reject" and method == "POST":
            action_id = parts[4]
            payload = parse_json_body(body)
            reason = payload.get("reason") if isinstance(payload.get("reason"), str) else None
            events = list(runtime.agent_service.reject_action(session_id, action_id, reason))
            session = runtime.agent_service.get_session_snapshot(session_id)
            return _json_response(HTTPStatus.OK, {"session": session, "events": events})

    return _json_response(HTTPStatus.NOT_FOUND, {"error": "Not found."})
