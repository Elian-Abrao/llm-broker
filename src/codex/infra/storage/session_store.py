from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ...bootstrap.config import KEYRING_SERVICE_NAME, KEYRING_USERNAME
from ...domain.auth import AuthSession


def _load_optional_keyring() -> Any | None:
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring


class FileSystemSessionStore:
    def __init__(
        self,
        file_path: Path,
        *,
        keyring_backend: Any | None = None,
        keyring_service: str = KEYRING_SERVICE_NAME,
        keyring_username: str = KEYRING_USERNAME,
        prefer_keyring: bool = True,
    ) -> None:
        self._file_path = file_path
        self._keyring_backend = keyring_backend if keyring_backend is not None else (_load_optional_keyring() if prefer_keyring else None)
        self._keyring_service = keyring_service
        self._keyring_username = keyring_username

    def load(self) -> AuthSession | None:
        session = self._load_from_keyring()
        if session:
            return session

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except FileNotFoundError:
            return None

        if not isinstance(parsed, dict):
            return None

        if parsed.get("storage") == "keyring":
            return None

        session = parsed.get("session")
        loaded = self._parse_session(session)
        if loaded and self._keyring_backend:
            self.save(loaded)
        return loaded

    def save(self, session: AuthSession) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if self._keyring_backend:
            self._keyring_backend.set_password(
                self._keyring_service,
                self._keyring_username,
                json.dumps(asdict(session)),
            )
            payload = {
                "version": 2,
                "storage": "keyring",
                "session": session.to_public_dict(),
            }
        else:
            payload = {
                "version": 1,
                "session": {
                    "provider": session.provider,
                    "accessToken": session.access_token,
                    "refreshToken": session.refresh_token,
                    "expiresAt": session.expires_at,
                    "updatedAt": session.updated_at,
                    "idToken": session.id_token,
                    "accountId": session.account_id,
                    "email": session.email,
                    "planType": session.plan_type,
                },
            }
        temp_path = self._file_path.with_suffix(f"{self._file_path.suffix}.tmp")
        temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
        temp_path.replace(self._file_path)

    def clear(self) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if self._keyring_backend:
            try:
                self._keyring_backend.delete_password(self._keyring_service, self._keyring_username)
            except Exception:
                pass
            payload = {"version": 2, "storage": "keyring"}
        else:
            payload = {"version": 1}
        temp_path = self._file_path.with_suffix(f"{self._file_path.suffix}.tmp")
        temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
        temp_path.replace(self._file_path)

    def _load_from_keyring(self) -> AuthSession | None:
        if not self._keyring_backend:
            return None
        try:
            raw = self._keyring_backend.get_password(self._keyring_service, self._keyring_username)
        except Exception:
            return None
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return self._parse_session(parsed)

    def _parse_session(self, session: object) -> AuthSession | None:
        if not isinstance(session, dict):
            return None

        access_token = session.get("accessToken", session.get("access_token"))
        refresh_token = session.get("refreshToken", session.get("refresh_token"))
        expires_at = session.get("expiresAt", session.get("expires_at"))
        updated_at = session.get("updatedAt", session.get("updated_at"))
        provider = session.get("provider")

        if not isinstance(provider, str):
            return None
        if not isinstance(access_token, str):
            return None
        if not isinstance(refresh_token, str):
            return None
        if not isinstance(expires_at, int):
            return None
        if not isinstance(updated_at, int):
            return None

        return AuthSession(
            provider=provider,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            updated_at=updated_at,
            id_token=session.get("idToken", session.get("id_token")),
            account_id=session.get("accountId", session.get("account_id")),
            email=session.get("email"),
            plan_type=session.get("planType", session.get("plan_type")),
        )
