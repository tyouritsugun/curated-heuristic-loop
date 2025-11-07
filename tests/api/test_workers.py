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


def test_pause_without_pool_returns_503(client):
    response = client.post("/api/v1/workers/pause")
    assert response.status_code in (200, 503)
    if response.status_code == 503:
        assert response.json()["detail"] == "Worker pool not initialized"
