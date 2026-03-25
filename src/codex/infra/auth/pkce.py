from __future__ import annotations

import base64
import hashlib
import os
from urllib.parse import quote_plus


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    verifier = _base64url(os.urandom(64))
    challenge = _base64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def generate_oauth_state() -> str:
    return _base64url(os.urandom(32))


def to_form_urlencoded(data: dict[str, str | int | bool | None]) -> str:
    pairs: list[str] = []
    for key, value in data.items():
        if value is None:
            continue
        pairs.append(f"{quote_plus(str(key))}={quote_plus(str(value))}")
    return "&".join(pairs)
