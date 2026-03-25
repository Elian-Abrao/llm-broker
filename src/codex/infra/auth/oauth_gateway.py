from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import urlencode

from ...bootstrap.config import (
    ACCESS_TOKEN_EXPIRY_BUFFER_MS,
    CODEX_CLIENT_ID,
    CODEX_ORIGINATOR,
    FALLBACK_EXPIRY_MS,
    OPENAI_AUTH_ISSUER,
)
from ...domain.auth import AuthSession, OAuthLoginTicket
from ...domain.errors import BrokerError
from .jwt_claims import (
    extract_jwt_expiry_ms,
    extract_openai_account_id,
    extract_openai_email,
    extract_openai_plan_type,
)
from .pkce import generate_oauth_state, generate_pkce_pair, to_form_urlencoded


@dataclass(frozen=True)
class OAuthProviderDefinition:
    id: str
    client_id: str
    issuer: str
    bind_host: str
    redirect_host: str
    redirect_port: int
    redirect_path: str
    scopes: tuple[str, ...]
    authorize_extra_params: dict[str, str]


CODEX_OAUTH_PROVIDER = OAuthProviderDefinition(
    id="codex",
    client_id=CODEX_CLIENT_ID,
    issuer=OPENAI_AUTH_ISSUER,
    bind_host="127.0.0.1",
    redirect_host="localhost",
    redirect_port=1455,
    redirect_path="/auth/callback",
    scopes=(
        "openid",
        "profile",
        "email",
        "offline_access",
        "api.connectors.read",
        "api.connectors.invoke",
    ),
    authorize_extra_params={
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": CODEX_ORIGINATOR,
    },
)


def build_redirect_uri(provider: OAuthProviderDefinition) -> str:
    return f"http://{provider.redirect_host}:{provider.redirect_port}{provider.redirect_path}"


def build_authorize_url(provider: OAuthProviderDefinition, challenge: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": build_redirect_uri(provider),
            "scope": " ".join(provider.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            **provider.authorize_extra_params,
        }
    )
    return f"{provider.issuer}/oauth/authorize?{query}"


def build_token_url(provider: OAuthProviderDefinition) -> str:
    return f"{provider.issuer}/oauth/token"


def _resolve_expiry_timestamp(
    *,
    now_ms: int,
    expires_in_seconds: int | None,
    access_token: str | None,
    id_token: str | None,
) -> int:
    explicit_expiry = (
        now_ms + expires_in_seconds * 1000 - ACCESS_TOKEN_EXPIRY_BUFFER_MS
        if isinstance(expires_in_seconds, int)
        else None
    )
    jwt_expiry = extract_jwt_expiry_ms(access_token) or extract_jwt_expiry_ms(id_token)
    return max(
        explicit_expiry or (jwt_expiry - ACCESS_TOKEN_EXPIRY_BUFFER_MS if jwt_expiry else now_ms + FALLBACK_EXPIRY_MS),
        now_ms + 30_000,
    )


class OpenAIOAuthGateway:
    def __init__(self, *, provider: OAuthProviderDefinition) -> None:
        self._provider = provider

    def create_login_ticket(self, *, now_ms: int, timeout_ms: int) -> OAuthLoginTicket:
        verifier, challenge = generate_pkce_pair()
        state = generate_oauth_state()
        return OAuthLoginTicket(
            id=str(uuid.uuid4()),
            state=state,
            verifier=verifier,
            challenge=challenge,
            redirect_uri=build_redirect_uri(self._provider),
            auth_url=build_authorize_url(self._provider, challenge, state),
            started_at=now_ms,
            expires_at=now_ms + timeout_ms,
        )

    def exchange_authorization_code(
        self,
        *,
        code: str,
        ticket: OAuthLoginTicket,
        now_ms: int,
    ) -> AuthSession:
        token_url = build_token_url(self._provider)
        encoded = to_form_urlencoded(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ticket.redirect_uri,
                "client_id": self._provider.client_id,
                "code_verifier": ticket.verifier,
            }
        ).encode("utf-8")
        req = request.Request(
            token_url,
            data=encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise BrokerError(502, f"OAuth token exchange failed ({exc.code}): {body}", body) from exc

        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise BrokerError(502, "OAuth response is missing access_token.")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise BrokerError(502, "OAuth response is missing refresh_token.")

        id_token = payload.get("id_token")
        return AuthSession(
            provider="codex",
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token if isinstance(id_token, str) and id_token.strip() else None,
            account_id=extract_openai_account_id(access_token) or extract_openai_account_id(id_token),
            email=extract_openai_email(id_token),
            plan_type=extract_openai_plan_type(id_token),
            expires_at=_resolve_expiry_timestamp(
                now_ms=now_ms,
                expires_in_seconds=payload.get("expires_in") if isinstance(payload.get("expires_in"), int) else None,
                access_token=access_token,
                id_token=id_token if isinstance(id_token, str) else None,
            ),
            updated_at=now_ms,
        )

    def refresh_session(self, *, session: AuthSession, now_ms: int) -> AuthSession:
        token_url = build_token_url(self._provider)
        body = json.dumps(
            {
                "client_id": self._provider.client_id,
                "grant_type": "refresh_token",
                "refresh_token": session.refresh_token,
            }
        ).encode("utf-8")
        req = request.Request(
            token_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise BrokerError(502, f"OAuth refresh failed ({exc.code}): {body_text}", body_text) from exc

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise BrokerError(502, "OAuth refresh response is missing access_token.")

        id_token = payload.get("id_token")
        return AuthSession(
            provider="codex",
            access_token=access_token,
            refresh_token=payload.get("refresh_token") if isinstance(payload.get("refresh_token"), str) else session.refresh_token,
            id_token=id_token if isinstance(id_token, str) and id_token.strip() else session.id_token,
            account_id=(
                extract_openai_account_id(access_token)
                or extract_openai_account_id(id_token if isinstance(id_token, str) else None)
                or session.account_id
            ),
            email=extract_openai_email(id_token if isinstance(id_token, str) else None) or session.email,
            plan_type=extract_openai_plan_type(id_token if isinstance(id_token, str) else None) or session.plan_type,
            expires_at=_resolve_expiry_timestamp(
                now_ms=now_ms,
                expires_in_seconds=payload.get("expires_in") if isinstance(payload.get("expires_in"), int) else None,
                access_token=access_token,
                id_token=id_token if isinstance(id_token, str) else session.id_token,
            ),
            updated_at=now_ms,
        )
