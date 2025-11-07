"""Telemetry snapshot tests."""
import time


def test_telemetry_snapshot_keys(client):
    time.sleep(0.2)  # Allow telemetry loop to gather at least one sample
    response = client.get("/api/v1/telemetry/snapshot")
    assert response.status_code == 200
    data = response.json()
    for key in ["queue", "worker_pool", "workers", "jobs"]:
        assert key in data
    assert isinstance(data["workers"], list)
    assert isinstance(data["jobs"], list)
