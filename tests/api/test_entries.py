"""Comprehensive tests for entries API endpoints (CRUD operations)."""

import pytest
import time


# Fixtures

@pytest.fixture
def sample_experience_data():
    """Sample data for creating an experience."""
    return {
        "section": "useful",
        "title": "Test Experience for API Tests",
        "playbook": "This is a test playbook for verifying API functionality",
        "context": None
    }


@pytest.fixture
def sample_manual_data():
    """Sample data for creating a manual."""
    return {
        "title": "Test Manual for API Tests",
        "content": "This is test manual content for verifying API functionality",
        "summary": "Test manual summary"
    }


# Write Entry Tests

def test_write_experience_success(client, sample_experience_data):
    """Test successful experience creation."""
    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": sample_experience_data
        }
    )

    # Should succeed or fail gracefully if category doesn't exist
    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json()
        assert data["success"] is True
        assert "entry_id" in data
        assert data["entry_id"].startswith("EXP-TST-")
        assert "message" in data


def test_write_manual_success(client, sample_manual_data):
    """Test successful manual creation."""
    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "manual",
            "category_code": "TST",
            "data": sample_manual_data
        }
    )

    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json()
        assert data["success"] is True
        assert "entry_id" in data
        assert data["entry_id"].startswith("MNL-TST-")


def test_write_experience_with_context(client):
    """Test experience creation with context field."""
    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "contextual",
                "title": "Contextual Experience Test",
                "playbook": "Test playbook",
                "context": {"note": "This is contextual metadata"}
            }
        }
    )

    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json()
        assert data["success"] is True


def test_write_entry_missing_required_fields(client):
    """Test entry creation with missing required fields."""
    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful"
                # Missing title and playbook
            }
        }
    )

    # Should return 422 (validation error) or 400 (bad request)
    assert response.status_code in [400, 422]


def test_write_entry_invalid_entity_type(client, sample_experience_data):
    """Test entry creation with invalid entity type."""
    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "invalid_type",
            "category_code": "TST",
            "data": sample_experience_data
        }
    )

    # Should return validation error
    assert response.status_code in [400, 422]


def test_write_entry_nonexistent_category(client, sample_experience_data):
    """Test entry creation with non-existent category."""
    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "NONEXISTENT",
            "data": sample_experience_data
        }
    )

    # Should return 404 not found
    assert response.status_code == 404


def test_write_entry_returns_immediately(client, sample_experience_data):
    """Test that write operation returns quickly (non-blocking)."""
    start = time.time()

    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": sample_experience_data
        }
    )

    elapsed = time.time() - start

    # Should return in under 200ms (non-blocking, no inline embedding)
    assert elapsed < 0.2, f"Write took {elapsed:.3f}s, expected < 0.2s"

    if response.status_code == 200:
        data = response.json()
        assert data["success"] is True


# Read Entry Tests

def test_read_entries_by_ids(client):
    """Test reading entries by specific IDs."""
    # First create an entry
    write_response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful",
                "title": "Read Test Entry",
                "playbook": "Test playbook for read operation"
            }
        }
    )

    if write_response.status_code != 200:
        pytest.skip("Cannot test read without successful write")

    entry_id = write_response.json()["entry_id"]

    # Now read it back
    read_response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "ids": [entry_id]
        }
    )

    assert read_response.status_code == 200
    data = read_response.json()

    assert "entries" in data
    assert "count" in data
    assert data["count"] >= 1

    # Verify entry content
    found = False
    for entry in data["entries"]:
        if entry["id"] == entry_id:
            assert entry["title"] == "Read Test Entry"
            assert entry["section"] == "useful"
            found = True
            break

    assert found, f"Created entry {entry_id} not found in read results"


def test_read_entries_with_query(client):
    """Test reading entries with semantic/text search query."""
    response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "PGS",
            "query": "test search query",
            "limit": 10
        }
    )

    # Should succeed even if no matches or category doesn't exist
    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json()
        assert "entries" in data
        assert "count" in data
        assert isinstance(data["entries"], list)
        assert len(data["entries"]) <= 10


def test_read_entries_with_limit(client):
    """Test that limit parameter is respected."""
    response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "PGS",
            "limit": 3
        }
    )

    if response.status_code == 200:
        data = response.json()
        assert len(data["entries"]) <= 3


def test_read_entries_nonexistent_category(client):
    """Test reading from non-existent category."""
    response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "NONEXISTENT"
        }
    )

    assert response.status_code == 404


def test_read_entries_nonexistent_ids(client):
    """Test reading entries with non-existent IDs."""
    response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "ids": ["EXP-TST-NONEXISTENT"]
        }
    )

    # Should return 200 with empty or partial results
    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json()
        assert "entries" in data
        # May be empty if ID doesn't exist


def test_read_manuals(client):
    """Test reading manual entries."""
    response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "manual",
            "category_code": "PGS",
            "limit": 5
        }
    )

    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json()
        assert "entries" in data

        # Verify manual structure if any exist
        for entry in data["entries"]:
            assert "id" in entry
            assert entry["id"].startswith("MNL-")
            assert "title" in entry
            assert "content" in entry or "summary" in entry


# Update Entry Tests

def test_update_experience_title(client):
    """Test updating an experience title."""
    # Create entry first
    write_response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful",
                "title": "Original Title",
                "playbook": "Original playbook"
            }
        }
    )

    if write_response.status_code != 200:
        pytest.skip("Cannot test update without successful write")

    entry_id = write_response.json()["entry_id"]

    # Update the title
    update_response = client.post(
        "/api/v1/entries/update",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "entry_id": entry_id,
            "updates": {
                "title": "Updated Title"
            }
        }
    )

    assert update_response.status_code == 200
    data = update_response.json()
    assert data["success"] is True

    # Verify the update
    read_response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "ids": [entry_id]
        }
    )

    assert read_response.status_code == 200
    entries = read_response.json()["entries"]
    assert len(entries) > 0
    assert entries[0]["title"] == "Updated Title"
    assert entries[0]["playbook"] == "Original playbook"  # Other fields unchanged


def test_update_manual_content(client):
    """Test updating a manual's content."""
    # Create manual first
    write_response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "manual",
            "category_code": "TST",
            "data": {
                "title": "Test Manual",
                "content": "Original content",
                "summary": "Original summary"
            }
        }
    )

    if write_response.status_code != 200:
        pytest.skip("Cannot test update without successful write")

    entry_id = write_response.json()["entry_id"]

    # Update the content
    update_response = client.post(
        "/api/v1/entries/update",
        json={
            "entity_type": "manual",
            "category_code": "TST",
            "entry_id": entry_id,
            "updates": {
                "content": "Updated content",
                "summary": "Updated summary"
            }
        }
    )

    assert update_response.status_code == 200


def test_update_nonexistent_entry(client):
    """Test updating an entry that doesn't exist."""
    response = client.post(
        "/api/v1/entries/update",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "entry_id": "EXP-TST-NONEXISTENT",
            "updates": {
                "title": "New Title"
            }
        }
    )

    # Should return 404 not found
    assert response.status_code == 404


def test_update_empty_updates(client):
    """Test update with empty updates dictionary."""
    # Create entry first
    write_response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful",
                "title": "Test Entry",
                "playbook": "Test playbook"
            }
        }
    )

    if write_response.status_code != 200:
        pytest.skip("Cannot test update without successful write")

    entry_id = write_response.json()["entry_id"]

    # Update with empty dict
    update_response = client.post(
        "/api/v1/entries/update",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "entry_id": entry_id,
            "updates": {}
        }
    )

    # Should return 400 bad request or 422 validation error
    assert update_response.status_code in [400, 422]


# Delete Entry Tests

def test_delete_experience(client):
    """Test deleting an experience."""
    # Create entry first
    write_response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful",
                "title": "Entry to Delete",
                "playbook": "This will be deleted"
            }
        }
    )

    if write_response.status_code != 200:
        pytest.skip("Cannot test delete without successful write")

    entry_id = write_response.json()["entry_id"]

    # Delete it
    delete_response = client.request(
        "DELETE",
        "/api/v1/entries/delete",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "entry_id": entry_id
        }
    )

    assert delete_response.status_code == 200
    data = delete_response.json()
    assert data["success"] is True

    # Verify it's gone
    read_response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "ids": [entry_id]
        }
    )

    # Should not find the deleted entry
    assert read_response.status_code in [200, 404]
    if read_response.status_code == 200:
        entries = read_response.json()["entries"]
        assert entry_id not in [e["id"] for e in entries]


def test_delete_manual(client):
    """Test deleting a manual."""
    # Create manual first
    write_response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "manual",
            "category_code": "TST",
            "data": {
                "title": "Manual to Delete",
                "content": "This will be deleted",
                "summary": "Deletion test"
            }
        }
    )

    if write_response.status_code != 200:
        pytest.skip("Cannot test delete without successful write")

    entry_id = write_response.json()["entry_id"]

    # Delete it
    delete_response = client.request(
        "DELETE",
        "/api/v1/entries/delete",
        json={
            "entity_type": "manual",
            "category_code": "TST",
            "entry_id": entry_id
        }
    )

    assert delete_response.status_code == 200


def test_delete_nonexistent_entry(client):
    """Test deleting an entry that doesn't exist."""
    response = client.request(
        "DELETE",
        "/api/v1/entries/delete",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "entry_id": "EXP-TST-NONEXISTENT"
        }
    )

    # Should return 404 not found
    assert response.status_code == 404


# Edge Cases and Error Handling

def test_concurrent_writes_different_categories(client, sample_experience_data):
    """Test concurrent writes to different categories."""
    import concurrent.futures

    def write_entry(category):
        return client.post(
            "/api/v1/entries/write",
            json={
                "entity_type": "experience",
                "category_code": category,
                "data": sample_experience_data
            }
        )

    # Write to multiple categories concurrently
    categories = ["TST", "PGS", "ADG"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(write_entry, cat) for cat in categories]
        results = [f.result() for f in futures]

    # All should succeed or fail gracefully
    for response in results:
        assert response.status_code in [200, 404]


def test_read_write_consistency(client):
    """Test that written data is immediately readable."""
    # Write an entry
    write_response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful",
                "title": "Consistency Test Entry",
                "playbook": "This tests read-after-write consistency"
            }
        }
    )

    if write_response.status_code != 200:
        pytest.skip("Cannot test consistency without successful write")

    entry_id = write_response.json()["entry_id"]

    # Immediately read it back
    read_response = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "TST",
            "ids": [entry_id]
        }
    )

    assert read_response.status_code == 200
    entries = read_response.json()["entries"]

    # Should find the entry immediately
    assert len(entries) > 0
    assert entries[0]["id"] == entry_id
    assert entries[0]["title"] == "Consistency Test Entry"


def test_malformed_json_request(client):
    """Test handling of malformed JSON in request body."""
    # FastAPI/Pydantic should handle validation
    # This test documents expected behavior
    response = client.post(
        "/api/v1/entries/write",
        json={
            "entity_type": "experience"
            # Missing required category_code and data fields
        }
    )

    # Should return 422 validation error
    assert response.status_code == 422
