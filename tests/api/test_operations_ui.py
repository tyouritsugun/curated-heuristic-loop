"""UI smoke tests for the operations dashboard."""
from contextlib import contextmanager
from pathlib import Path
import io
import zipfile

from src.api import server as api_server
from src.common.storage.schema import JobHistory


@contextmanager
def _session():
    if api_server.db is None:
        raise RuntimeError("Database not initialized")
    session = api_server.db.get_session()
    try:
        yield session
    finally:
        session.close()


def _clear_jobs():
    with _session() as session:
        session.query(JobHistory).delete()
        session.commit()


def test_operations_page_renders(client):
    response = client.get("/operations")
    assert response.status_code == 200
    assert "Operations" in response.text


def test_trigger_operation_from_ui(client):
    _clear_jobs()
    response = client.post(
        "/ui/operations/run/import",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "ops-refresh"
    with _session() as session:
        assert session.query(JobHistory).count() >= 1


def test_worker_action_handles_missing_pool(client):
    response = client.post(
        "/ui/workers/pause",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Worker pool not initialized" in response.text


def test_jobs_partial_loads(client):
    response = client.get(
        "/ui/operations/jobs",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Job History" in response.text


def test_telemetry_sse_stream(client):
    response = client.get("/ui/stream/telemetry", params={"cycles": 1})
    assert response.status_code == 200
    assert "event: queue" in response.text
    assert "event: index" in response.text
    assert "event: controls" in response.text


def test_operations_controls_show_last_run(client):
    _clear_jobs()
    with _session() as session:
        session.add(
            JobHistory(
                job_id="test-job",
                job_type="import",
                status="succeeded",
                requested_by="unit-test",
                payload="{}",
                result="{}",
                created_at="2025-11-07T12:00:00+00:00",
                started_at="2025-11-07T12:00:05+00:00",
                finished_at="2025-11-07T12:00:10+00:00",
            )
        )
        session.commit()

    response = client.get(
        "/ui/operations/controls",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert 'data-job-type="import"' in response.text
    assert 'data-job-status="succeeded"' in response.text


def test_index_snapshot_download(client):
    index_dir = Path(api_server.config.faiss_index_path)
    index_dir.mkdir(parents=True, exist_ok=True)
    created_dummy = False
    if not any(index_dir.iterdir()):
        (index_dir / "dummy.index").write_bytes(b"dummy")
        created_dummy = True

    response = client.get("/ui/index/download")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "application/zip"

    if created_dummy:
        (index_dir / "dummy.index").unlink(missing_ok=True)


def test_index_snapshot_upload_roundtrip(client):
    index_dir = Path(api_server.config.faiss_index_path)
    index_dir.mkdir(parents=True, exist_ok=True)
    files = [path for path in index_dir.iterdir() if path.is_file()]
    created_dummy = False
    if not files:
        dummy = index_dir / "dummy.index"
        dummy.write_bytes(b"dummy")
        files = [dummy]
        created_dummy = True

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in files:
            archive.writestr(path.name, path.read_bytes())
    buffer.seek(0)

    response = client.post(
        "/ui/index/upload",
        headers={"HX-Request": "true"},
        files={"snapshot": ("snapshot.zip", buffer.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    assert "Uploaded snapshot" in response.text

    if created_dummy:
        (index_dir / "dummy.index").unlink(missing_ok=True)
