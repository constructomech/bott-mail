"""Bearer token authentication with constant-time comparison."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException


class TokenAuth:
    """Validates a single read bearer token.

    Phase 1 exposes only a read token. The token *identity* (not value) is
    returned so it can be recorded in audit logs.
    """

    def __init__(self, read_token: str, token_id: str = "read_default") -> None:
        self._read_token = read_token
        self._token_id = token_id

    def verify(self, authorization: str | None) -> str:
        """Return the token_id if the Authorization header is valid.

        Raises HTTPException(401) on any failure.
        """
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")

        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(status_code=401, detail="Malformed Authorization header")

        presented = parts[1].strip()
        if not presented or not hmac.compare_digest(presented, self._read_token):
            raise HTTPException(status_code=401, detail="Invalid token")

        return self._token_id


def make_dependency(auth: TokenAuth):
    """Build a FastAPI dependency that yields the validated token_id."""

    async def _dep(authorization: str | None = Header(default=None)) -> str:
        return auth.verify(authorization)

    return _dep
