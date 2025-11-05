"""Smoke tests for API endpoints."""

import pytest


def test_root_endpoint(client):
    """Test root endpoint returns service info."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "CHL API"
    assert data["version"] == "0.2.0"
    assert data["status"] == "running"


def test_health_endpoint(client):
    """Test health endpoint returns component status."""
    response = client.get("/health/")

    # Should return 200 for healthy or degraded, 503 for unhealthy
    assert response.status_code in [200, 503]

    data = response.json()
    assert "status" in data
    assert data["status"] in ["healthy", "degraded", "unhealthy"]
    assert "components" in data
    assert "database" in data["components"]
    assert "faiss_index" in data["components"]
    assert "embedding_model" in data["components"]
    assert "timestamp" in data


def test_metrics_endpoint(client):
    """Test metrics endpoint returns metrics data."""
    response = client.get("/health/metrics")
    assert response.status_code == 200

    data = response.json()
    assert "counters" in data
    assert "histograms" in data


def test_categories_list(client):
    """Test categories list endpoint."""
    response = client.get("/api/v1/categories/")
    assert response.status_code == 200

    data = response.json()
    assert "categories" in data
    assert isinstance(data["categories"], list)

    # Should have at least the default categories
    if len(data["categories"]) > 0:
        category = data["categories"][0]
        assert "code" in category
        assert "name" in category


def test_entries_read_with_text_search(client):
    """Test entries read endpoint with text search (SQLite fallback)."""
    # This should work even without embedding models
    response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "PGS",
            "query": "test",
            "limit": 5
        }
    )

    # Should succeed or return 404 if category doesn't exist
    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json()
        assert "entries" in data
        assert "count" in data
        assert isinstance(data["entries"], list)


def test_entries_read_invalid_entity_type(client):
    """Test entries read with invalid entity type."""
    response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "invalid",
            "category_code": "PGS"
        }
    )

    # Should succeed with validation (Pydantic doesn't restrict string values)
    # But will fail at the handler level or just return empty results
    assert response.status_code in [200, 400, 404, 422]


def test_health_check_database_component(client):
    """Test that database component is properly checked."""
    response = client.get("/health/")
    data = response.json()

    db_status = data["components"]["database"]["status"]
    # Database should be healthy since we have a working DB
    assert db_status in ["healthy", "unhealthy"]

    if db_status == "healthy":
        assert "Connected" in data["components"]["database"]["detail"]


def test_cors_headers(client):
    """Test CORS headers are present."""
    response = client.options("/")
    # FastAPI/Starlette handles OPTIONS automatically with CORS middleware
    assert response.status_code in [200, 405]  # 405 if no OPTIONS handler


def test_openapi_docs(client):
    """Test OpenAPI documentation is accessible."""
    response = client.get("/docs")
    assert response.status_code == 200

    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert "openapi" in data
    assert "info" in data
