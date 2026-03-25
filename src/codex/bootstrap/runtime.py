from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..app import AgentService, AuthService, ChatService
from ..app.provider_registry import ProviderEntry, ProviderRegistry
from ..infra.auth.callback_server import LoopbackCallbackServerFactory
from ..infra.auth.oauth_gateway import CODEX_OAUTH_PROVIDER, OpenAIOAuthGateway
from ..infra.providers.codex.http_gateway import CodexHttpGateway
from ..infra.providers.gemini_cli.auth_gateway import GEMINI_CLI_PROVIDER, GoogleOAuthGateway
from ..infra.providers.gemini_cli.http_gateway import GeminiCliHttpGateway, DEFAULT_GEMINI_MODEL, GEMINI_MODELS
from ..infra.storage.session_store import FileSystemSessionStore
from ..infra.tools import ReadFileTool, ShellTool, WriteFileTool
from ..domain.codex import build_capabilities
from .config import BrokerConfig, load_config


@dataclass
class BrokerRuntime:
    config: BrokerConfig
    auth_service: AuthService  # Default/Codex auth (kept for backward compat)
    chat_service: ChatService
    agent_service: AgentService
    registry: ProviderRegistry


def _build_codex_entry(resolved: BrokerConfig) -> tuple[ProviderEntry, AuthService]:
    auth_store = FileSystemSessionStore(
        resolved.auth_store_path,
        prefer_keyring=resolved.prefer_keyring,
    )
    oauth_gateway = OpenAIOAuthGateway(provider=CODEX_OAUTH_PROVIDER)
    callback_factory = LoopbackCallbackServerFactory(
        host=CODEX_OAUTH_PROVIDER.bind_host,
        port=CODEX_OAUTH_PROVIDER.redirect_port,
        callback_path=CODEX_OAUTH_PROVIDER.redirect_path,
        cancel_path="/auth/cancel",
        timeout_seconds=resolved.login_timeout_ms / 1000,
    )
    auth_service = AuthService(
        session_store=auth_store,
        oauth_gateway=oauth_gateway,
        callback_server_factory=callback_factory,
        login_timeout_ms=resolved.login_timeout_ms,
        min_refresh_delay_ms=resolved.min_refresh_delay_ms,
    )
    auth_service.initialize()
    gateway = CodexHttpGateway(base_url=resolved.codex_base_url, user_agent=resolved.user_agent)
    capabilities = build_capabilities(authenticated=bool(auth_service.get_state().session), account_email=None)
    capabilities["provider"] = "codex"
    entry = ProviderEntry(
        provider_id="codex",
        auth_service=auth_service,
        gateway=gateway,
        capabilities=capabilities,
    )
    return entry, auth_service


def _build_gemini_entry(resolved: BrokerConfig) -> ProviderEntry:
    auth_store = FileSystemSessionStore(
        resolved.gemini_auth_store_path,
        prefer_keyring=resolved.prefer_keyring,
        keyring_service="llm-broker-gemini",
        keyring_username="default",
    )
    oauth_gateway = GoogleOAuthGateway(provider=GEMINI_CLI_PROVIDER)
    callback_factory = LoopbackCallbackServerFactory(
        host=GEMINI_CLI_PROVIDER.bind_host,
        port=GEMINI_CLI_PROVIDER.redirect_port,
        callback_path=GEMINI_CLI_PROVIDER.redirect_path,
        cancel_path="/auth/cancel",
        timeout_seconds=resolved.login_timeout_ms / 1000,
    )
    auth_service = AuthService(
        session_store=auth_store,
        oauth_gateway=oauth_gateway,
        callback_server_factory=callback_factory,
        login_timeout_ms=resolved.login_timeout_ms,
        min_refresh_delay_ms=resolved.min_refresh_delay_ms,
    )
    auth_service.initialize()
    gateway = GeminiCliHttpGateway(base_url=resolved.gemini_base_url, user_agent=resolved.user_agent)
    return ProviderEntry(
        provider_id="gemini_cli",
        auth_service=auth_service,
        gateway=gateway,
        capabilities={
            "provider": "gemini_cli",
            "billingMode": "usage",
            "requiresAuth": True,
            "authenticated": bool(auth_service.get_state().session),
            "accountEmail": None,
            "defaultModel": DEFAULT_GEMINI_MODEL,
            "models": GEMINI_MODELS,
        },
    )


def create_runtime(config: BrokerConfig | None = None) -> BrokerRuntime:
    resolved = config or load_config()

    registry = ProviderRegistry()

    codex_entry, codex_auth = _build_codex_entry(resolved)
    registry.register(codex_entry, default=True)

    gemini_entry = _build_gemini_entry(resolved)
    registry.register(gemini_entry)

    chat_service = ChatService(registry=registry)
    agent_service = AgentService(
        chat_service=chat_service,
        tools=[ReadFileTool(), WriteFileTool(), ShellTool()],
        workspace_root=Path.cwd(),
    )

    return BrokerRuntime(
        config=resolved,
        auth_service=codex_auth,
        chat_service=chat_service,
        agent_service=agent_service,
        registry=registry,
    )
