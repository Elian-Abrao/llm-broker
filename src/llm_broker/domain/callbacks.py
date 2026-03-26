from __future__ import annotations

from urllib import parse

from .auth import CallbackPayload
from .errors import BrokerError


def _normalize_input(value: str) -> parse.ParseResult:
    trimmed = value.strip()
    if not trimmed:
        raise BrokerError(400, "Paste the full redirect URL containing code and state.")

    parsed = parse.urlparse(trimmed)
    if parsed.scheme and parsed.netloc:
        return parsed

    query_only = trimmed if trimmed.startswith("?") else f"?{trimmed}"
    return parse.urlparse(f"http://localhost{query_only}")


def parse_manual_callback_input(value: str, expected_state: str) -> CallbackPayload:
    parsed = _normalize_input(value)
    params = parse.parse_qs(parsed.query)
    code = (params.get("code", [""])[0] or "").strip()
    state = (params.get("state", [""])[0] or "").strip()

    if not code:
        raise BrokerError(400, "Redirect URL is missing the authorization code.")
    if not state:
        raise BrokerError(400, "Redirect URL is missing the state parameter.")
    if state != expected_state:
        raise BrokerError(400, "OAuth state mismatch. The login flow was rejected to prevent CSRF.")

    return CallbackPayload(code=code, state=state)
