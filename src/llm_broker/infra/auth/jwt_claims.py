from __future__ import annotations

import base64
import json
from typing import Any


JsonDict = dict[str, Any]


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def decode_jwt_payload(token: str) -> JsonDict | None:
    parts = token.split(".")
    if len(parts) < 2 or not parts[1]:
        return None

    try:
        payload = _decode_base64url(parts[1]).decode("utf-8")
        parsed = json.loads(payload)
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def extract_token_claims(token: str | None) -> JsonDict:
    if not token:
        return {}
    return decode_jwt_payload(token) or {}


def extract_jwt_expiry_ms(token: str | None) -> int | None:
    claims = extract_token_claims(token)
    exp = claims.get("exp")
    return exp * 1000 if isinstance(exp, int) else None


def extract_openai_account_id(token: str | None) -> str | None:
    claims = extract_token_claims(token)
    auth = claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        return None
    account_id = auth.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id.strip() else None


def extract_openai_email(token: str | None) -> str | None:
    claims = extract_token_claims(token)
    email = claims.get("email")
    if isinstance(email, str) and email.strip():
        return email

    profile = claims.get("https://api.openai.com/profile")
    if isinstance(profile, dict):
        profile_email = profile.get("email")
        if isinstance(profile_email, str) and profile_email.strip():
            return profile_email

    return None


def extract_openai_plan_type(token: str | None) -> str | None:
    claims = extract_token_claims(token)
    auth = claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        return None
    plan = auth.get("chatgpt_plan_type")
    return plan if isinstance(plan, str) and plan.strip() else None
