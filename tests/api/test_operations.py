"""Operations API tests covering trigger, conflict, and cancellation."""
import time


def wait_for_status(client, job_id, expected, timeout=5.0):
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/operations/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] == expected:
            return body
        time.sleep(0.1)
    raise AssertionError(f"Job {job_id} did not reach {expected}")


def test_trigger_import_job(client):
    resp = client.post(
        "/api/v1/operations/import",
        json={"payload": {"note": "pytest"}},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    status = wait_for_status(client, job_id, "succeeded")
    assert status["job_type"] == "import"
    assert status["status"] == "succeeded"


def test_operation_conflict_while_running(client):
    first = client.post(
        "/api/v1/operations/export",
        json={"payload": {"_test_delay": 1.0}},
    )
    assert first.status_code == 200
    job_id = first.json()["job_id"]

    conflict = client.post(
        "/api/v1/operations/export",
        json={"payload": {"note": "second"}},
    )
    assert conflict.status_code == 409

    wait_for_status(client, job_id, "succeeded")


def test_cancel_operation(client):
    queued = client.post(
        "/api/v1/operations/index",
        json={"payload": {"_test_delay": 1.0}},
    )
    assert queued.status_code == 200
    job_id = queued.json()["job_id"]

    time.sleep(0.1)
    cancel_resp = client.post(f"/api/v1/operations/jobs/{job_id}/cancel")
    assert cancel_resp.status_code == 200
    payload = wait_for_status(client, job_id, "cancelled")
    assert payload["status"] == "cancelled"
