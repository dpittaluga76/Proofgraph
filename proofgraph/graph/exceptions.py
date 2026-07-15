from typing import Any


class GraphAPIError(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def as_payload(self) -> dict[str, object]:
        error: dict[str, object] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}
