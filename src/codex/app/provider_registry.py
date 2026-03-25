from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .auth_service import AuthService
from ..domain.errors import BrokerError
from ..domain.ports import LLMGatewayPort


@dataclass
class ProviderEntry:
    provider_id: str
    auth_service: AuthService
    gateway: LLMGatewayPort
    capabilities: dict[str, Any] = field(default_factory=dict)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderEntry] = {}
        self._default: str | None = None

    def register(self, entry: ProviderEntry, *, default: bool = False) -> None:
        self._providers[entry.provider_id] = entry
        if default or self._default is None:
            self._default = entry.provider_id

    def get(self, provider_id: str) -> ProviderEntry:
        entry = self._providers.get(provider_id)
        if entry is None:
            available = ", ".join(self._providers) or "none"
            raise BrokerError(400, f"Unknown provider '{provider_id}'. Available: {available}.")
        return entry

    def get_default(self) -> ProviderEntry:
        if not self._default:
            raise BrokerError(503, "No LLM providers are configured.")
        return self.get(self._default)

    def list_ids(self) -> list[str]:
        return list(self._providers)

    def list_capabilities(self) -> list[dict[str, Any]]:
        return [entry.capabilities for entry in self._providers.values()]
