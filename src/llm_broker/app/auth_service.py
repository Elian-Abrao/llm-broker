from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..domain.auth import AuthSession, AuthState, CallbackPayload, OAuthLoginTicket
from ..domain.callbacks import parse_manual_callback_input
from ..domain.errors import BrokerError
from ..domain.ports import CallbackServerFactoryPort, CallbackServerHandlePort, OAuthGatewayPort, SessionStorePort


@dataclass
class PendingLoginContext:
    ticket: OAuthLoginTicket
    callback_server: CallbackServerHandlePort | None = None
    timeout_handle: threading.Timer | None = None
    completing: bool = False


class AuthService:
    def __init__(
        self,
        *,
        session_store: SessionStorePort,
        oauth_gateway: OAuthGatewayPort,
        callback_server_factory: CallbackServerFactoryPort,
        login_timeout_ms: int,
        min_refresh_delay_ms: int,
        now=None,
    ) -> None:
        self._session_store = session_store
        self._oauth_gateway = oauth_gateway
        self._callback_server_factory = callback_server_factory
        self._login_timeout_ms = login_timeout_ms
        self._min_refresh_delay_ms = min_refresh_delay_ms
        self._now = now or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._active_login: PendingLoginContext | None = None
        self._refresh_timer: threading.Timer | None = None
        self._is_refreshing = False
        self._session: AuthSession | None = None

    def initialize(self) -> None:
        session = self._session_store.load()
        with self._lock:
            self._session = session
        if session:
            self._schedule_refresh(session)

    def get_state(self) -> AuthState:
        with self._lock:
            active_login = self._active_login
            session = self._session
            is_refreshing = self._is_refreshing
        return AuthState(
            is_refreshing=is_refreshing,
            session=session,
            active_login=active_login.ticket if active_login else None,
        )

    def start_login(self) -> OAuthLoginTicket:
        with self._lock:
            if self._active_login:
                return self._active_login.ticket

        ticket = self._oauth_gateway.create_login_ticket(
            now_ms=self._now(),
            timeout_ms=self._login_timeout_ms,
        )

        pending = PendingLoginContext(ticket=ticket)

        timeout = threading.Timer(self._login_timeout_ms / 1000, self._clear_pending_login, args=(ticket.id,))
        timeout.daemon = True
        pending.timeout_handle = timeout
        timeout.start()

        try:
            callback_server = self._callback_server_factory.create(
                expected_state=ticket.state,
                success_title="Codex-Bridge connected",
                success_message="Your Codex session is ready. You can close this window and return to Codex-Bridge. The terminal or app will continue automatically.",
                on_callback=lambda payload: self.finish_login_from_callback(ticket.id, payload),
            )
            callback_server.start()
            pending.callback_server = callback_server
        except Exception:
            pending.callback_server = None

        with self._lock:
            self._active_login = pending

        return ticket

    def complete_manual_login(self, redirect_url: str) -> None:
        with self._lock:
            pending = self._active_login
        if not pending:
            raise BrokerError(409, "There is no active OAuth login to complete.")
        payload = parse_manual_callback_input(redirect_url, pending.ticket.state)
        self.finish_login_from_callback(pending.ticket.id, payload)

    def finish_login_from_callback(self, login_id: str, payload: CallbackPayload) -> None:
        with self._lock:
            pending = self._active_login
            if not pending or pending.ticket.id != login_id:
                return
            if pending.completing:
                return
            pending.completing = True

        try:
            session = self._oauth_gateway.exchange_authorization_code(
                code=payload.code,
                ticket=pending.ticket,
                now_ms=self._now(),
            )
            with self._lock:
                self._session = session
            self._session_store.save(session)
            self._schedule_refresh(session)
        finally:
            self._clear_pending_login(login_id)

    def logout(self) -> None:
        with self._lock:
            pending = self._active_login
            self._active_login = None
            self._session = None
            refresh_timer = self._refresh_timer
            self._refresh_timer = None

        if pending and pending.timeout_handle:
            pending.timeout_handle.cancel()
        if pending and pending.callback_server:
            pending.callback_server.close()
        if refresh_timer:
            refresh_timer.cancel()
        self._session_store.clear()

    def get_valid_session(self) -> AuthSession:
        with self._lock:
            session = self._session
        if not session:
            raise BrokerError(401, "No authenticated session is available. Please log in first.")
        if session.expires_at <= self._now():
            self.refresh_session()
            with self._lock:
                session = self._session
        if not session:
            raise BrokerError(401, "No authenticated session is available. Please log in first.")
        return session

    def refresh_session(self) -> None:
        with self._lock:
            session = self._session
        if not session:
            raise BrokerError(401, "No authenticated session is available. Please log in first.")

        with self._refresh_lock:
            with self._lock:
                self._is_refreshing = True
            try:
                refreshed = self._oauth_gateway.refresh_session(
                    session=session,
                    now_ms=self._now(),
                )
                with self._lock:
                    self._session = refreshed
                self._session_store.save(refreshed)
                self._schedule_refresh(refreshed)
            finally:
                with self._lock:
                    self._is_refreshing = False

    def _schedule_refresh(self, session: AuthSession) -> None:
        with self._lock:
            if self._refresh_timer:
                self._refresh_timer.cancel()
            delay_ms = max(self._min_refresh_delay_ms, session.expires_at - self._now())
            timer = threading.Timer(delay_ms / 1000, self._refresh_from_timer)
            timer.daemon = True
            self._refresh_timer = timer
            timer.start()

    def _refresh_from_timer(self) -> None:
        try:
            self.refresh_session()
        except Exception:
            return

    def _clear_pending_login(self, login_id: str) -> None:
        with self._lock:
            pending = self._active_login
            if not pending or pending.ticket.id != login_id:
                return
            self._active_login = None

        if pending.timeout_handle:
            pending.timeout_handle.cancel()
        if pending.callback_server:
            pending.callback_server.close()
