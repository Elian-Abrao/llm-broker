from __future__ import annotations


class BrokerError(RuntimeError):
    def __init__(self, status_code: int, message: str, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or ""
