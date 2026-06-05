from app.auth import TokenAuth

import pytest
from fastapi import HTTPException


def test_valid_token():
    auth = TokenAuth("secret-token", token_id="read_default")
    assert auth.verify("Bearer secret-token") == "read_default"


def test_missing_header():
    auth = TokenAuth("secret-token")
    with pytest.raises(HTTPException) as exc:
        auth.verify(None)
    assert exc.value.status_code == 401


def test_malformed_header():
    auth = TokenAuth("secret-token")
    with pytest.raises(HTTPException):
        auth.verify("secret-token")  # missing "Bearer "
    with pytest.raises(HTTPException):
        auth.verify("Basic secret-token")


def test_wrong_token():
    auth = TokenAuth("secret-token")
    with pytest.raises(HTTPException):
        auth.verify("Bearer nope")


def test_empty_token():
    auth = TokenAuth("secret-token")
    with pytest.raises(HTTPException):
        auth.verify("Bearer ")
