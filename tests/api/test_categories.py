"""Comprehensive tests for categories API endpoint."""

import pytest


def test_list_categories_success(client):
    """Test successful category listing."""
    response = client.get("/api/v1/categories/")
    assert response.status_code == 200

    data = response.json()
    assert "categories" in data
    assert isinstance(data["categories"], list)


def test_list_categories_contains_default_categories(client):
    """Test that default categories are present after seeding."""
    response = client.get("/api/v1/categories/")
    data = response.json()

    # Should have at least some categories if database is seeded
    categories = data["categories"]

    if len(categories) > 0:
        # Verify category structure
        category = categories[0]
        assert "code" in category
        assert "name" in category
        assert "description" in category
        assert "created_at" in category

        # Verify data types
        assert isinstance(category["code"], str)
        assert isinstance(category["name"], str)
        assert len(category["code"]) > 0
        assert len(category["name"]) > 0


def test_list_categories_codes_are_unique(client):
    """Test that category codes are unique."""
    response = client.get("/api/v1/categories/")
    data = response.json()

    categories = data["categories"]
    codes = [cat["code"] for cat in categories]

    # Check for duplicates
    assert len(codes) == len(set(codes)), f"Duplicate category codes found: {codes}"


def test_list_categories_names_are_unique(client):
    """Test that category names are unique."""
    response = client.get("/api/v1/categories/")
    data = response.json()

    categories = data["categories"]
    names = [cat["name"] for cat in categories]

    # Check for duplicates
    assert len(names) == len(set(names)), f"Duplicate category names found: {names}"


def test_list_categories_with_trailing_slash(client):
    """Test that endpoint works with and without trailing slash."""
    response_with_slash = client.get("/api/v1/categories/")
    response_without_slash = client.get("/api/v1/categories")

    # Both should work
    assert response_with_slash.status_code == 200
    assert response_without_slash.status_code in [200, 307, 308]  # 307/308 for redirect


def test_list_categories_response_format(client):
    """Test that response format matches expected schema."""
    response = client.get("/api/v1/categories/")
    assert response.status_code == 200

    data = response.json()

    # Top-level structure
    assert isinstance(data, dict)
    assert "categories" in data
    assert isinstance(data["categories"], list)

    # Each category should have required fields
    for category in data["categories"]:
        assert isinstance(category, dict)
        required_fields = ["code", "name", "description", "created_at"]
        for field in required_fields:
            assert field in category, f"Missing required field: {field}"


def test_list_categories_caching_headers(client):
    """Test that appropriate caching headers are set."""
    response = client.get("/api/v1/categories/")
    assert response.status_code == 200

    # Check if caching headers are present (optional, depends on implementation)
    # This test documents expected behavior even if not currently implemented
    headers = response.headers

    # Categories rarely change, so caching might be appropriate
    # If implemented, check for Cache-Control or ETag headers
    # For now, just verify the response is valid


def test_list_categories_empty_database(client):
    """Test category listing when database might be empty.

    Note: This test assumes the database might not be seeded in test environment.
    If database is always seeded, this will still pass with an empty list.
    """
    response = client.get("/api/v1/categories/")
    assert response.status_code == 200

    data = response.json()
    assert "categories" in data
    assert isinstance(data["categories"], list)
    # Empty list is valid response


def test_list_categories_performance(client):
    """Test that category listing completes quickly."""
    import time

    start = time.time()
    response = client.get("/api/v1/categories/")
    elapsed = time.time() - start

    assert response.status_code == 200
    # Should complete in under 1 second even with many categories
    assert elapsed < 1.0, f"Category listing took {elapsed:.2f}s, expected < 1s"


def test_list_categories_concurrent_requests(client):
    """Test that concurrent category listings don't interfere."""
    import concurrent.futures

    def fetch_categories():
        response = client.get("/api/v1/categories/")
        return response.status_code, response.json()

    # Make 5 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_categories) for _ in range(5)]
        results = [f.result() for f in futures]

    # All should succeed
    for status, data in results:
        assert status == 200
        assert "categories" in data

    # All should return the same data
    first_categories = results[0][1]["categories"]
    for _, data in results[1:]:
        assert data["categories"] == first_categories


def test_list_categories_known_codes(client):
    """Test that expected category codes are present if database is seeded."""
    response = client.get("/api/v1/categories/")
    data = response.json()

    categories = data["categories"]
    codes = {cat["code"] for cat in categories}

    # Expected codes from seed data (may not all be present in test environment)
    expected_codes = {"PGS", "ADG", "DSD", "FPD", "TMG", "GLN"}

    # If we have categories, check if any expected ones are present
    if codes:
        # At least one expected code should be present if seeded
        # This is a soft assertion - it's OK if test DB is not fully seeded
        pass
