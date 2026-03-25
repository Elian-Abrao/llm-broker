from __future__ import annotations

from dataclasses import dataclass, field
from os import getenv
from pathlib import Path

from ..domain.codex import DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT

BRIDGE_SERVICE_NAME = "codex-bridge"
BRIDGE_API_PREFIX = "/v1"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 47831
DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_USER_AGENT = "codex-bridge/python"
KEYRING_SERVICE_NAME = "codex-bridge"
KEYRING_USERNAME = "default"
OPENAI_AUTH_ISSUER = "https://auth.openai.com"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_ORIGINATOR = "codex_cli_rs"
ACCESS_TOKEN_EXPIRY_BUFFER_MS = 5 * 60 * 1000
LOGIN_TIMEOUT_MS = 5 * 60 * 1000
MIN_REFRESH_DELAY_MS = 15_000
FALLBACK_EXPIRY_MS = 55 * 60 * 1000
GEMINI_CALLBACK_PORT = 9004
# Gemini CLI OAuth credentials (public installed-app credentials from the Gemini CLI open-source project).
# Set via env vars. The values can be found in the Gemini CLI public repository.
GEMINI_CLIENT_ID = getenv("GEMINI_CLIENT_ID", "")
GEMINI_CLIENT_SECRET = getenv("GEMINI_CLIENT_SECRET", "")

DEFAULT_CODEX_MODELS = [
    {
        "id": "gpt-5.4",
        "label": "gpt-5.4",
        "description": "Balanced default for Codex-backed chat workflows.",
        "recommended": True,
    },
    {
        "id": "gpt-5",
        "label": "gpt-5",
        "description": "General-purpose GPT-5 model for broader compatibility.",
    },
    {
        "id": "gpt-5-mini",
        "label": "gpt-5-mini",
        "description": "Lower-latency GPT-5 option for lighter tasks.",
    },
]


def default_auth_store_path() -> Path:
    return Path.home() / ".codex-bridge" / "auth" / "codex-session.json"


def default_gemini_auth_store_path() -> Path:
    return Path.home() / ".codex-bridge" / "auth" / "gemini-session.json"


@dataclass(frozen=True)
class BrokerConfig:
    bind_host: str = DEFAULT_BIND_HOST
    bind_port: int = DEFAULT_BIND_PORT
    auth_store_path: Path = field(default_factory=default_auth_store_path)
    codex_base_url: str = DEFAULT_CODEX_BASE_URL
    gemini_base_url: str = DEFAULT_GEMINI_BASE_URL
    gemini_auth_store_path: Path = field(default_factory=default_gemini_auth_store_path)
    user_agent: str = DEFAULT_USER_AGENT
    prefer_keyring: bool = True
    login_timeout_ms: int = LOGIN_TIMEOUT_MS
    min_refresh_delay_ms: int = MIN_REFRESH_DELAY_MS


def load_config(
    *,
    host: str | None = None,
    port: int | None = None,
    auth_store_path: str | None = None,
    codex_base_url: str | None = None,
    user_agent: str | None = None,
    prefer_keyring: bool | None = None,
) -> BrokerConfig:
    raw_port = str(port or getenv("CODEX_BRIDGE_PORT", "")).strip()
    parsed_port = DEFAULT_BIND_PORT
    if raw_port:
        try:
            parsed_port = int(raw_port)
        except ValueError:
            parsed_port = DEFAULT_BIND_PORT

    raw_store_path = auth_store_path or getenv("CODEX_BRIDGE_AUTH_STORE_PATH", "")
    store_path = Path(raw_store_path).expanduser() if raw_store_path.strip() else default_auth_store_path()

    return BrokerConfig(
        bind_host=(host or getenv("CODEX_BRIDGE_HOST", "")).strip() or DEFAULT_BIND_HOST,
        bind_port=parsed_port,
        auth_store_path=store_path,
        codex_base_url=(codex_base_url or getenv("CODEX_BASE_URL", "")).strip() or DEFAULT_CODEX_BASE_URL,
        gemini_base_url=getenv("GEMINI_BASE_URL", "").strip() or DEFAULT_GEMINI_BASE_URL,
        gemini_auth_store_path=default_gemini_auth_store_path(),
        user_agent=(user_agent or getenv("CODEX_BRIDGE_USER_AGENT", "")).strip() or DEFAULT_USER_AGENT,
        prefer_keyring=(
            prefer_keyring
            if prefer_keyring is not None
            else getenv("CODEX_BRIDGE_DISABLE_KEYRING", "").strip() not in {"1", "true", "yes"}
        ),
        login_timeout_ms=LOGIN_TIMEOUT_MS,
        min_refresh_delay_ms=MIN_REFRESH_DELAY_MS,
    )
