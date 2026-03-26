from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CallbackPayload:
    code: str
    state: str


@dataclass(frozen=True, slots=True)
class OAuthLoginTicket:
    id: str
    state: str
    verifier: str
    challenge: str
    redirect_uri: str
    auth_url: str
    started_at: int
    expires_at: int
    manual_fallback: bool = True
    provider: str = "codex"

    def to_dict(self, *, include_started_at: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "provider": self.provider,
            "authUrl": self.auth_url,
            "redirectUri": self.redirect_uri,
            "expiresAt": self.expires_at,
            "manualFallback": self.manual_fallback,
        }
        if include_started_at:
            payload["startedAt"] = self.started_at
        return payload


@dataclass(frozen=True, slots=True)
class AuthSession:
    provider: str
    access_token: str
    refresh_token: str
    expires_at: int
    updated_at: int
    id_token: str | None = None
    account_id: str | None = None
    email: str | None = None
    plan_type: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "accountId": self.account_id,
            "email": self.email,
            "planType": self.plan_type,
            "expiresAt": self.expires_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class AuthState:
    is_refreshing: bool
    session: AuthSession | None = None
    active_login: OAuthLoginTicket | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"isRefreshing": self.is_refreshing}
        if self.active_login:
            payload["activeLogin"] = self.active_login.to_dict(include_started_at=True)
        if self.session:
            payload["session"] = self.session.to_public_dict()
        return payload
