"""Shared pytest fixtures."""
import os
import tempfile

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "mail.sqlite")


@pytest.fixture
def sample_eml():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample_plain.eml")
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def html_eml():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample_html.eml")
    with open(path, "rb") as f:
        return f.read()
