"""Pytest fixtures for API tests."""

import os
import pytest
import contextlib
from typing import Iterator
from fastapi.testclient import TestClient
from src.api.server import app


@pytest.fixture
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    """Create a test client for the API.

    Honors marker @pytest.mark.cpu_only to start the server in CPU-only mode.
    Also honors legacy marker @pytest.mark.sqlite_only for backward compatibility.
    """
    # Detect marker to toggle CPU-only mode (support both old and new names)
    cpu_only = (request.node.get_closest_marker("cpu_only") is not None or
                request.node.get_closest_marker("sqlite_only") is not None)

    # Preserve and set environment for this test only
    prev = os.environ.get("CHL_SEARCH_MODE")
    try:
        if cpu_only:
            os.environ["CHL_SEARCH_MODE"] = "cpu"
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
