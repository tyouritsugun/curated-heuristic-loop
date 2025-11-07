"""Smoke tests for the settings UI endpoints."""
from pathlib import Path

CREDENTIAL_FILE = Path("credentials/curated-heuristic-loop-2c8a35dde6e9.json").resolve()


def test_settings_page_renders(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert "Settings" in response.text


def test_settings_path_form_updates_metadata(client):
    response = client.post(
        "/ui/settings/credentials/path",
        data={"path": str(CREDENTIAL_FILE)},
    )
    assert response.status_code == 200
    snapshot = client.get("/api/v1/settings/").json()
    assert snapshot["credentials"]["path"] == str(CREDENTIAL_FILE)


def test_settings_upload_flow_saves_file(client):
    payload = CREDENTIAL_FILE.read_bytes()
    files = {"credential_file": ("ui-creds.json", payload, "application/json")}
    response = client.post(
        "/ui/settings/credentials/upload",
        files=files,
    )
    assert response.status_code == 200
    snapshot = client.get("/api/v1/settings/").json()
    saved_path = Path(snapshot["credentials"]["path"])
    assert saved_path.exists()
    try:
        assert saved_path.read_bytes() == payload
    finally:
        saved_path.unlink(missing_ok=True)
