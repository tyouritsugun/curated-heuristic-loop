"""Pytest fixtures for API tests."""

import os

os.environ.setdefault("CHL_OPERATIONS_MODE", "noop")

import pytest
from fastapi.testclient import TestClient
from src.api_server import app


@pytest.fixture
def client():
    """Create a test client for the API."""
    with TestClient(app) as test_client:
        yield test_client
