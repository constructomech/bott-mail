from app.auth import TokenAuth, TokenSpec

import pytest
from fastapi import HTTPException


def test_valid_token():
    auth = TokenAuth(
        [TokenSpec("read_default", "secret-token", frozenset({"messages:read"}))]
    )
    ctx = auth.verify("Bearer secret-token")
    assert ctx.token_id == "read_default"
    assert ctx.has_scope("messages:read")


def test_wildcard_scope_matches_any_scope():
    auth = TokenAuth([TokenSpec("admin", "admin-token", frozenset({"*"}))])
    ctx = auth.verify("Bearer admin-token")
    assert ctx.has_scope("messages:archive")


def test_read_token_lacks_archive_scope():
    auth = TokenAuth(
        [TokenSpec("read", "read-token", frozenset({"messages:read"}))]
    )
    ctx = auth.verify("Bearer read-token")
    assert not ctx.has_scope("messages:archive")


def test_empty_token_list_rejects_all_tokens():
    auth = TokenAuth([])
    with pytest.raises(HTTPException) as exc:
        auth.verify("Bearer anything")
    assert exc.value.status_code == 401


def test_missing_header():
    auth = TokenAuth([TokenSpec("read", "secret-token", frozenset({"messages:read"}))])
    with pytest.raises(HTTPException) as exc:
        auth.verify(None)
    assert exc.value.status_code == 401


def test_malformed_header():
    auth = TokenAuth([TokenSpec("read", "secret-token", frozenset({"messages:read"}))])
    with pytest.raises(HTTPException):
        auth.verify("secret-token")  # missing "Bearer "
    with pytest.raises(HTTPException):
        auth.verify("Basic secret-token")


def test_wrong_token():
    auth = TokenAuth([TokenSpec("read", "secret-token", frozenset({"messages:read"}))])
    with pytest.raises(HTTPException):
        auth.verify("Bearer nope")


def test_empty_token():
    auth = TokenAuth([TokenSpec("read", "secret-token", frozenset({"messages:read"}))])
    with pytest.raises(HTTPException):
        auth.verify("Bearer ")
