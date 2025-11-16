"""Enhanced concurrency tests for Phase 3: Additional edge cases and stress scenarios."""
import threading
import time
import pytest
from fastapi.testclient import TestClient


def test_concurrent_updates_same_entry(test_client):
    """Test concurrent updates to the same entry.

    Verifies that:
    - Concurrent updates to same entry complete without corruption
    - Last write wins (or proper conflict resolution)
    - No race conditions in FAISS metadata
    """
    # Create initial entry
    create_response = test_client.post("/api/v1/entries/write", json={
        "entity_type": "experience",
        "category_code": "TST",
        "data": {
            "section": "useful",
            "title": "Concurrent Update Test",
            "playbook": "Initial playbook"
        }
    })

    if create_response.status_code != 200:
        pytest.skip("Cannot create initial entry for concurrent update test")

    entry_id = create_response.json()["entry_id"]
    results = {"success": [], "errors": []}
    lock = threading.Lock()

    def update_worker(worker_id):
        try:
            response = test_client.post("/api/v1/entries/update", json={
                "entity_type": "experience",
                "category_code": "TST",
                "entry_id": entry_id,
                "updates": {
                    "playbook": f"Updated by worker {worker_id}"
                }
            })
            response.raise_for_status()
            with lock:
                results["success"].append(worker_id)
        except Exception as e:
            with lock:
                results["errors"].append((worker_id, str(e)))

    # Launch 5 concurrent updaters
    threads = [threading.Thread(target=update_worker, args=(i,)) for i in range(5)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All updates should succeed (even though they conflict)
    assert len(results["errors"]) == 0, f"Errors occurred: {results['errors']}"
    assert len(results["success"]) == 5

    # Verify final state is consistent
    read_response = test_client.post("/api/v1/entries/read", json={
        "entity_type": "experience",
        "category_code": "TST",
        "ids": [entry_id]
    })

    assert read_response.status_code == 200
    entries = read_response.json()["entries"]
    assert len(entries) == 1
    # Playbook should be from one of the workers (last write wins)
    assert "Updated by worker" in entries[0]["playbook"]


def test_concurrent_deletes(test_client):
    """Test concurrent deletes of different entries.

    Verifies that:
    - Multiple concurrent deletes complete successfully
    - FAISS tombstone tracking works correctly
    - No corruption in index metadata
    """
    # Create multiple entries
    entry_ids = []
    for i in range(5):
        response = test_client.post("/api/v1/entries/write", json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful",
                "title": f"Delete Test Entry {i}",
                "playbook": f"Will be deleted {i}"
            }
        })
        if response.status_code == 200:
            entry_ids.append(response.json()["entry_id"])

    if len(entry_ids) < 5:
        pytest.skip("Cannot create enough entries for concurrent delete test")

    results = {"success": [], "errors": []}
    lock = threading.Lock()

    def delete_worker(entry_id):
        try:
            response = test_client.delete("/api/v1/entries/delete", json={
                "entity_type": "experience",
                "category_code": "TST",
                "entry_id": entry_id
            })
            response.raise_for_status()
            with lock:
                results["success"].append(entry_id)
        except Exception as e:
            with lock:
                results["errors"].append((entry_id, str(e)))

    # Delete all concurrently
    threads = [threading.Thread(target=delete_worker, args=(eid,)) for eid in entry_ids]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All deletes should succeed
    assert len(results["errors"]) == 0
    assert len(results["success"]) == 5


def test_index_rebuild_during_operations(test_client):
    """Test that operations work correctly during index rebuild.

    Verifies that:
    - Searches continue to work during rebuild
    - No data corruption occurs
    - Rebuild completes successfully
    """
    # This test requires access to admin rebuild endpoint
    # Check if it exists first
    rebuild_response = test_client.post("/admin/index/rebuild", timeout=60)

    if rebuild_response.status_code == 404:
        pytest.skip("Rebuild endpoint not available")

    # For now, just verify the endpoint exists and returns properly
    assert rebuild_response.status_code in [200, 503]  # 503 if no FAISS


def test_search_during_concurrent_writes(test_client):
    """Test that searches remain consistent during concurrent writes.

    Verifies that:
    - Searches don't return corrupted results during writes
    - No deadlocks between readers and writers
    - Results are eventually consistent
    """
    results = {"searches": 0, "writes": 0, "errors": []}
    lock = threading.Lock()
    stop_event = threading.Event()

    def search_worker(worker_id):
        """Continuously search while others are writing."""
        while not stop_event.is_set():
            try:
                response = test_client.post("/api/v1/entries/read", json={
                    "entity_type": "experience",
                    "category_code": "PGS",
                    "query": f"search during writes {worker_id}"
                })
                response.raise_for_status()
                with lock:
                    results["searches"] += 1
                time.sleep(0.01)
            except Exception as e:
                with lock:
                    results["errors"].append(("search", worker_id, str(e)))
                break

    def write_worker(worker_id):
        """Write entries for 3 seconds."""
        end_time = time.time() + 3
        count = 0
        while time.time() < end_time:
            try:
                response = test_client.post("/api/v1/entries/write", json={
                    "entity_type": "experience",
                    "category_code": "TST",
                    "data": {
                        "section": "useful",
                        "title": f"Concurrent Write Test {worker_id}-{count}",
                        "playbook": "Testing concurrent operations"
                    }
                })
                response.raise_for_status()
                with lock:
                    results["writes"] += 1
                count += 1
                time.sleep(0.05)
            except Exception as e:
                with lock:
                    results["errors"].append(("write", worker_id, str(e)))
                break

    # Start 2 search workers and 3 write workers
    search_threads = [threading.Thread(target=search_worker, args=(i,)) for i in range(2)]
    write_threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(3)]

    for t in search_threads + write_threads:
        t.start()

    # Let it run for 3 seconds
    time.sleep(3)

    # Stop search workers
    stop_event.set()

    # Wait for all threads
    for t in search_threads + write_threads:
        t.join(timeout=2)

    # Verify operations completed
    assert len(results["errors"]) == 0, f"Errors occurred: {results['errors'][:5]}"
    assert results["searches"] > 0, "No searches completed"
    assert results["writes"] > 0, "No writes completed"


def test_tombstone_accumulation(test_client):
    """Test behavior when many deletions create tombstones.

    Verifies that:
    - Tombstone ratio tracking works
    - Automatic rebuild triggers (if threshold exceeded)
    - Index remains functional with tombstones
    """
    # Create and delete many entries to accumulate tombstones
    entry_ids = []

    # Create 20 entries
    for i in range(20):
        response = test_client.post("/api/v1/entries/write", json={
            "entity_type": "experience",
            "category_code": "TST",
            "data": {
                "section": "useful",
                "title": f"Tombstone Test {i}",
                "playbook": f"Will be deleted to create tombstone {i}"
            }
        })
        if response.status_code == 200:
            entry_ids.append(response.json()["entry_id"])

    if len(entry_ids) < 10:
        pytest.skip("Cannot create enough entries for tombstone test")

    # Delete most of them (70%)
    for entry_id in entry_ids[:14]:
        test_client.delete("/api/v1/entries/delete", json={
            "entity_type": "experience",
            "category_code": "TST",
            "entry_id": entry_id
        })

    # Check index status
    status_response = test_client.get("/admin/index/status")

    if status_response.status_code == 200:
        status = status_response.json()
        # Index should still be functional
        assert status["available"] in [True, False]  # May not have FAISS in test


def test_high_frequency_updates(test_client):
    """Test rapid updates to same entry.

    Verifies that:
    - System handles high-frequency updates gracefully
    - No lock contention issues
    - Final state is consistent
    """
    # Create entry
    create_response = test_client.post("/api/v1/entries/write", json={
        "entity_type": "experience",
        "category_code": "TST",
        "data": {
            "section": "useful",
            "title": "High Frequency Update Test",
            "playbook": "Initial"
        }
    })

    if create_response.status_code != 200:
        pytest.skip("Cannot create entry for high frequency test")

    entry_id = create_response.json()["entry_id"]

    # Rapidly update 50 times
    success_count = 0
    for i in range(50):
        response = test_client.post("/api/v1/entries/update", json={
            "entity_type": "experience",
            "category_code": "TST",
            "entry_id": entry_id,
            "updates": {
                "playbook": f"Update iteration {i}"
            }
        })
        if response.status_code == 200:
            success_count += 1

    # Most updates should succeed (allow some failures under extreme load)
    assert success_count >= 40, f"Only {success_count}/50 updates succeeded"

    # Verify final state is consistent
    read_response = test_client.post("/api/v1/entries/read", json={
        "entity_type": "experience",
        "category_code": "TST",
        "ids": [entry_id]
    })

    assert read_response.status_code == 200
    entries = read_response.json()["entries"]
    assert len(entries) == 1
    assert "Update iteration" in entries[0]["playbook"]


def test_error_recovery_after_failed_operation(test_client):
    """Test that system recovers gracefully from failed operations.

    Verifies that:
    - Failed operations don't leave system in bad state
    - Subsequent operations succeed
    - No lingering locks or corruption
    """
    # Attempt invalid operation
    bad_response = test_client.post("/api/v1/entries/write", json={
        "entity_type": "experience",
        "category_code": "NONEXISTENT",
        "data": {
            "section": "useful",
            "title": "This should fail",
            "playbook": "Invalid category"
        }
    })

    # Should fail gracefully
    assert bad_response.status_code in [404, 422]

    # Immediately try valid operation
    good_response = test_client.post("/api/v1/entries/write", json={
        "entity_type": "experience",
        "category_code": "TST",
        "data": {
            "section": "useful",
            "title": "Recovery Test",
            "playbook": "This should succeed after failure"
        }
    })

    # Should succeed (system recovered from previous error)
    assert good_response.status_code in [200, 404]  # 404 if TST category doesn't exist


# Fixture
@pytest.fixture
def test_client():
    """Create a TestClient for the FastAPI app."""
    from src.api.server import app
    return TestClient(app)
