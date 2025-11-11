"""Worker control API tests."""

def test_worker_status_returns_queue_counts(client):
    response = client.get("/api/v1/workers/")
    assert response.status_code == 200
    data = response.json()
    assert "queue" in data
    assert "pending" in data["queue"]
    assert "failed" in data["queue"]
    # Worker pool may be absent in test env; workers should then be null or dict
    assert "workers" in data
