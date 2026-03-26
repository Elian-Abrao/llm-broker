from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import urlencode

from ....bootstrap.config import (
    ACCESS_TOKEN_EXPIRY_BUFFER_MS,
    FALLBACK_EXPIRY_MS,
    GEMINI_CLIENT_ID,
    GEMINI_CLIENT_SECRET,
)
from ....domain.auth import AuthSession, OAuthLoginTicket
from ....domain.errors import BrokerError
from ....infra.auth.pkce import generate_oauth_state, generate_pkce_pair, to_form_urlencoded
from ....infra.auth.jwt_claims import extract_jwt_expiry_ms
GEMINI_AUTH_BASE = "https://accounts.google.com/o/oauth2/auth"
GEMINI_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Exact scopes used by the Gemini CLI (google-gemini/gemini-cli)
GEMINI_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
)
GEMINI_REDIRECT_HOST = "127.0.0.1"
GEMINI_REDIRECT_PORT = 9004
GEMINI_REDIRECT_PATH = "/oauth2callback"


@dataclass(frozen=True)
class GoogleOAuthProviderDefinition:
    id: str
    client_id: str
    client_secret: str
    auth_base_url: str
    token_url: str
    bind_host: str
    redirect_host: str
    redirect_port: int
    redirect_path: str
    scopes: tuple[str, ...]


GEMINI_CLI_PROVIDER = GoogleOAuthProviderDefinition(
    id="gemini_cli",
    client_id=GEMINI_CLIENT_ID,
    client_secret=GEMINI_CLIENT_SECRET,
    auth_base_url=GEMINI_AUTH_BASE,
    token_url=GEMINI_TOKEN_URL,
    bind_host=GEMINI_REDIRECT_HOST,
    redirect_host=GEMINI_REDIRECT_HOST,
    redirect_port=GEMINI_REDIRECT_PORT,
    redirect_path=GEMINI_REDIRECT_PATH,
    scopes=GEMINI_SCOPES,
)


def _build_redirect_uri(provider: GoogleOAuthProviderDefinition) -> str:
    return f"http://{provider.redirect_host}:{provider.redirect_port}{provider.redirect_path}"


def _build_authorize_url(provider: GoogleOAuthProviderDefinition, challenge: str, state: str) -> str:
    query = urlencode({
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": _build_redirect_uri(provider),
        "scope": " ".join(provider.scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    })
    return f"{provider.auth_base_url}?{query}"


def _resolve_expiry(*, now_ms: int, expires_in: int | None, access_token: str | None) -> int:
    if isinstance(expires_in, int):
        return now_ms + expires_in * 1000 - ACCESS_TOKEN_EXPIRY_BUFFER_MS
    jwt_exp = extract_jwt_expiry_ms(access_token)
    if jwt_exp:
        return jwt_exp - ACCESS_TOKEN_EXPIRY_BUFFER_MS
    return now_ms + FALLBACK_EXPIRY_MS


class GoogleOAuthGateway:
    def __init__(self, *, provider: GoogleOAuthProviderDefinition) -> None:
        self._provider = provider

    def create_login_ticket(self, *, now_ms: int, timeout_ms: int) -> OAuthLoginTicket:
        verifier, challenge = generate_pkce_pair()
        state = generate_oauth_state()
        return OAuthLoginTicket(
            id=str(uuid.uuid4()),
            state=state,
            verifier=verifier,
            challenge=challenge,
            redirect_uri=_build_redirect_uri(self._provider),
            auth_url=_build_authorize_url(self._provider, challenge, state),
            started_at=now_ms,
            expires_at=now_ms + timeout_ms,
            provider=self._provider.id,
        )

    def exchange_authorization_code(self, *, code: str, ticket: OAuthLoginTicket, now_ms: int) -> AuthSession:
        body = to_form_urlencoded({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": ticket.redirect_uri,
            "client_id": self._provider.client_id,
            "client_secret": self._provider.client_secret,
            "code_verifier": ticket.verifier,
        }).encode("utf-8")
        req = request.Request(
            self._provider.token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise BrokerError(502, f"Google OAuth token exchange failed ({exc.code}): {body_text}") from exc

        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise BrokerError(502, "Google OAuth response is missing access_token.")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise BrokerError(502, "Google OAuth response is missing refresh_token.")

        id_token = payload.get("id_token")
        email = _extract_google_email(id_token)
        expires_in = payload.get("expires_in")
        return AuthSession(
            provider=self._provider.id,
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token if isinstance(id_token, str) else None,
            account_id=None,
            email=email,
            plan_type=None,
            expires_at=_resolve_expiry(
                now_ms=now_ms,
                expires_in=expires_in if isinstance(expires_in, int) else None,
                access_token=access_token,
            ),
            updated_at=now_ms,
        )

    def refresh_session(self, *, session: AuthSession, now_ms: int) -> AuthSession:
        body = to_form_urlencoded({
            "grant_type": "refresh_token",
            "refresh_token": session.refresh_token,
            "client_id": self._provider.client_id,
            "client_secret": self._provider.client_secret,
        }).encode("utf-8")
        req = request.Request(
            self._provider.token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise BrokerError(502, f"Google OAuth refresh failed ({exc.code}): {body_text}") from exc

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise BrokerError(502, "Google OAuth refresh response is missing access_token.")

        id_token = payload.get("id_token")
        new_refresh = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        return AuthSession(
            provider=self._provider.id,
            access_token=access_token,
            refresh_token=new_refresh if isinstance(new_refresh, str) else session.refresh_token,
            id_token=id_token if isinstance(id_token, str) else session.id_token,
            account_id=session.account_id,
            email=_extract_google_email(id_token) or session.email,
            plan_type=session.plan_type,
            expires_at=_resolve_expiry(
                now_ms=now_ms,
                expires_in=expires_in if isinstance(expires_in, int) else None,
                access_token=access_token,
            ),
            updated_at=now_ms,
        )


def _extract_google_email(id_token: str | None) -> str | None:
    """Extract email claim from Google ID token (JWT) without verifying signature."""
    if not isinstance(id_token, str) or not id_token.strip():
        return None
    import base64
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        return claims.get("email") if isinstance(claims.get("email"), str) else None
    except Exception:
        return None
