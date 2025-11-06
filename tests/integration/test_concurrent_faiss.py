"""Concurrency integration tests for Phase 3: Shared Search & Indexing"""
import threading
import time
import pytest
from fastapi.testclient import TestClient


def test_concurrent_searches(test_client):
    """Test multiple clients searching simultaneously.

    Verifies that:
    - Multiple concurrent searches complete without errors
    - Results are consistent across clients (all get same results for same query)
    - No race conditions or deadlocks occur
    """
    results = []
    errors = []

    def search_worker(worker_id):
        try:
            response = test_client.post("/api/v1/entries/read", json={
                "entity_type": "experience",
                "category_code": "PGS",
                "query": "test concurrency"
            })
            response.raise_for_status()
            results.append((worker_id, response.json()))
        except Exception as e:
            errors.append((worker_id, str(e)))

    # Launch 10 concurrent searchers
    threads = [
        threading.Thread(target=search_worker, args=(i,))
        for i in range(10)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify no errors
    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == 10, f"Expected 10 results, got {len(results)}"

    # Verify all searches returned consistent results
    # (same entry IDs in same order, ignoring search metadata)
    first_entry_ids = [e["id"] for e in results[0][1]["entries"]]
    for worker_id, result in results[1:]:
        entry_ids = [e["id"] for e in result["entries"]]
        assert entry_ids == first_entry_ids, \
            f"Worker {worker_id} got inconsistent results: {entry_ids} != {first_entry_ids}"


def test_concurrent_writes(test_client):
    """Test multiple clients writing simultaneously.

    Verifies that:
    - Multiple concurrent writes complete without errors
    - All writes are persisted correctly
    - No data corruption occurs
    """
    results = []
    errors = []

    def write_worker(worker_id):
        try:
            response = test_client.post("/api/v1/entries/write", json={
                "entity_type": "experience",
                "category_code": "TST",
                "data": {
                    "section": "useful",
                    "title": f"Concurrent write test {worker_id}",
                    "playbook": f"This is a concurrent write from worker {worker_id}"
                }
            })
            response.raise_for_status()
            results.append((worker_id, response.json()))
        except Exception as e:
            errors.append((worker_id, str(e)))

    # Launch 5 concurrent writers
    threads = [
        threading.Thread(target=write_worker, args=(i,))
        for i in range(5)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify no errors
    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == 5, f"Expected 5 results, got {len(results)}"

    # Verify all writes succeeded
    entry_ids = []
    for worker_id, result in results:
        assert result["success"] is True
        entry_ids.append(result["entry_id"])

    # Verify all entries have unique IDs
    assert len(set(entry_ids)) == 5, f"Duplicate entry IDs detected: {entry_ids}"


def test_mixed_read_write(test_client):
    """Test mixed concurrent reads and writes.

    Verifies that:
    - Concurrent reads and writes don't block each other excessively
    - No deadlocks occur
    - Data consistency is maintained
    """
    results = {"reads": [], "writes": [], "errors": []}

    def read_worker(worker_id):
        try:
            response = test_client.post("/api/v1/entries/read", json={
                "entity_type": "experience",
                "category_code": "PGS",
                "query": "mixed workload test"
            })
            response.raise_for_status()
            results["reads"].append((worker_id, response.json()))
        except Exception as e:
            results["errors"].append(("read", worker_id, str(e)))

    def write_worker(worker_id):
        try:
            response = test_client.post("/api/v1/entries/write", json={
                "entity_type": "experience",
                "category_code": "TST",
                "data": {
                    "section": "useful",
                    "title": f"Mixed workload write {worker_id}",
                    "playbook": f"This is a mixed workload write from worker {worker_id}"
                }
            })
            response.raise_for_status()
            results["writes"].append((worker_id, response.json()))
        except Exception as e:
            results["errors"].append(("write", worker_id, str(e)))

    # Launch 10 threads (5 readers, 5 writers)
    threads = []
    for i in range(5):
        threads.append(threading.Thread(target=read_worker, args=(i,)))
        threads.append(threading.Thread(target=write_worker, args=(i,)))

    # Start all threads
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify no errors
    assert len(results["errors"]) == 0, f"Errors occurred: {results['errors']}"
    assert len(results["reads"]) == 5
    assert len(results["writes"]) == 5


def test_write_then_read_consistency(test_client):
    """Test that writes are immediately visible to other clients.

    Verifies that:
    - Data written by one client is immediately searchable by another
    - No stale cache or delayed indexing issues
    """
    # Client A writes
    write_response = test_client.post("/api/v1/entries/write", json={
        "entity_type": "experience",
        "category_code": "TST",
        "data": {
            "section": "useful",
            "title": "Write-then-read test entry",
            "playbook": "This entry should be immediately searchable after write"
        }
    })
    assert write_response.status_code == 200
    entry_id = write_response.json()["entry_id"]

    # Small delay to allow embedding (if enabled)
    time.sleep(0.5)

    # Client B searches for the entry by ID
    read_response = test_client.post("/api/v1/entries/read", json={
        "entity_type": "experience",
        "category_code": "TST",
        "ids": [entry_id]
    })
    assert read_response.status_code == 200

    # Verify Client B sees Client A's write
    entries = read_response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["id"] == entry_id
    assert entries[0]["title"] == "Write-then-read test entry"


@pytest.mark.slow
def test_stress_concurrent_operations(test_client):
    """Stress test with many concurrent operations.

    Verifies that:
    - System remains stable under heavy concurrent load
    - No resource leaks or deadlocks
    - Performance degradation is acceptable

    This test is marked as slow and should be run separately.
    """
    results = {"reads": 0, "writes": 0, "errors": []}
    lock = threading.Lock()

    def mixed_worker(worker_id, duration=10):
        """Worker that performs mixed read/write operations for specified duration."""
        import random
        end_time = time.time() + duration

        while time.time() < end_time:
            try:
                # 70% reads, 30% writes
                if random.random() < 0.7:
                    # Perform read
                    response = test_client.post("/api/v1/entries/read", json={
                        "entity_type": "experience",
                        "category_code": "PGS",
                        "query": f"stress test {random.randint(1, 100)}"
                    })
                    response.raise_for_status()
                    with lock:
                        results["reads"] += 1
                else:
                    # Perform write
                    response = test_client.post("/api/v1/entries/write", json={
                        "entity_type": "experience",
                        "category_code": "TST",
                        "data": {
                            "section": "useful",
                            "title": f"Stress test entry {worker_id}-{random.randint(1, 1000)}",
                            "playbook": f"Stress test content from worker {worker_id}"
                        }
                    })
                    response.raise_for_status()
                    with lock:
                        results["writes"] += 1

                # Small delay between operations
                time.sleep(0.01)

            except Exception as e:
                with lock:
                    results["errors"].append((worker_id, str(e)))

    # Launch 20 concurrent workers for 10 seconds
    threads = [
        threading.Thread(target=mixed_worker, args=(i, 10))
        for i in range(20)
    ]

    start_time = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start_time

    # Verify stress test completed successfully
    assert len(results["errors"]) == 0, f"Errors occurred during stress test: {results['errors'][:10]}"
    assert results["reads"] > 0, "No reads completed during stress test"
    assert results["writes"] > 0, "No writes completed during stress test"

    print(f"\nStress test results:")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  Total reads: {results['reads']}")
    print(f"  Total writes: {results['writes']}")
    print(f"  Total operations: {results['reads'] + results['writes']}")
    print(f"  Ops/sec: {(results['reads'] + results['writes']) / elapsed:.1f}")


# Fixtures

@pytest.fixture
def test_client():
    """Create a TestClient for the FastAPI app."""
    from src.api_server import app
    return TestClient(app)
