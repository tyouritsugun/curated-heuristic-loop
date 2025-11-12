"""Pytest fixtures for API tests."""

import os

os.environ.setdefault("CHL_OPERATIONS_MODE", "noop")

import pytest
import contextlib
from typing import Iterator
from fastapi.testclient import TestClient
from src.api_server import app


@pytest.fixture
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    """Create a test client for the API.

    Honors marker @pytest.mark.sqlite_only to start the server in CPU-only mode.
    """
    # Detect marker to toggle CPU-only mode
    sqlite_only = request.node.get_closest_marker("sqlite_only") is not None

    # Preserve and set environment for this test only
    prev = os.environ.get("CHL_SEARCH_MODE")
    try:
        if sqlite_only:
            os.environ["CHL_SEARCH_MODE"] = "sqlite_only"
        else:
            if "CHL_SEARCH_MODE" in os.environ:
                os.environ.pop("CHL_SEARCH_MODE", None)
        with TestClient(app) as test_client:
            yield test_client
    finally:
        if prev is not None:
            os.environ["CHL_SEARCH_MODE"] = prev
        else:
            os.environ.pop("CHL_SEARCH_MODE", None)
