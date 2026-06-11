"""Bearer token authentication with constant-time comparison."""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class AuthContext:
    token_id: str
    scopes: frozenset[str]

    def has_scope(self, required: str) -> bool:
        return "*" in self.scopes or required in self.scopes


@dataclass(frozen=True)
class TokenSpec:
    token_id: str
    token: str
    scopes: frozenset[str]


class TokenAuth:
    """Validates scoped bearer tokens.

    The token *identity* (not value) is returned so it can be recorded in audit
    logs. Write endpoints must use ``verify_write`` so the read token cannot
    mutate mail.
    """

    def __init__(self, tokens: list[TokenSpec]) -> None:
        self._tokens = [t for t in tokens if t.token]

    def verify(self, authorization: str | None) -> AuthContext:
        """Return the auth context if the Authorization header is valid.

        Raises HTTPException(401) on any failure.
        """
        presented = self._presented_token(authorization)
        for spec in self._tokens:
            if hmac.compare_digest(presented, spec.token):
                return AuthContext(token_id=spec.token_id, scopes=spec.scopes)
        raise HTTPException(status_code=401, detail="Invalid token")

    @staticmethod
    def _presented_token(authorization: str | None) -> str:
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")

        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(status_code=401, detail="Malformed Authorization header")

        return parts[1].strip()


def make_dependency(auth: TokenAuth):
    """Build a FastAPI dependency that yields the validated token_id."""

    async def _dep(authorization: str | None = Header(default=None)) -> AuthContext:
        return auth.verify(authorization)

    return _dep


def require_scope(auth: TokenAuth, scope: str):
    """Build a FastAPI dependency requiring a specific scope."""

    async def _dep(authorization: str | None = Header(default=None)) -> AuthContext:
        ctx = auth.verify(authorization)
        if not ctx.has_scope(scope):
            raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")
        return ctx

    return _dep
