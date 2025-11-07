"""Smoke tests for the settings UI endpoints."""
import json
from pathlib import Path

from src.storage.schema import Setting
import src.api_server as api_server

CREDENTIAL_FILE = Path("credentials/curated-heuristic-loop-2c8a35dde6e9.json").resolve()


def _clear_settings_table():
    if api_server.db is None:
        return
    session = api_server.db.get_session()
    try:
        session.query(Setting).delete()
        session.commit()
    finally:
        session.close()


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


def test_diagnostics_panel_endpoint(client):
    response = client.get("/ui/settings/diagnostics", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Connectivity" in response.text


def test_diagnostics_probe_requires_credentials(client):
    _clear_settings_table()
    response = client.post(
        "/ui/settings/diagnostics",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "No credentials configured" in response.text


def test_diagnostics_probe_revalidates_when_credentials_present(client):
    client.post(
        "/ui/settings/credentials/path",
        data={"path": str(CREDENTIAL_FILE)},
    )
    response = client.post(
        "/ui/settings/diagnostics",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Connectivity check refreshed" in response.text
    assert response.headers.get("HX-Trigger") == "settings-changed"


def test_audit_log_panel_renders(client):
    # Ensure at least one audit entry exists
    client.post(
        "/ui/settings/credentials/path",
        data={"path": str(CREDENTIAL_FILE)},
    )
    response = client.get("/ui/settings/audit-log", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Recent Audit Log" in response.text


def test_backup_restore_flow(client):
    _clear_settings_table()
    backup = {
        "credentials": {"path": str(CREDENTIAL_FILE)},
        "sheets": {
            "spreadsheet_id": "sheet-123",
            "experiences_tab": "Exp",
            "manuals_tab": "Manuals",
            "categories_tab": "Cats",
        },
        "models": {
            "embedding_repo": "qwen",
            "embedding_quant": "Q8",
            "reranker_repo": "reranker",
            "reranker_quant": "Q4",
        },
    }
    response = client.post(
        "/ui/settings/backup/restore",
        data={"backup_json": json.dumps(backup)},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Restored sections" in response.text

    snapshot = client.get("/api/v1/settings/").json()
    assert snapshot["credentials"]["path"] == str(CREDENTIAL_FILE)
    assert snapshot["sheets"]["spreadsheet_id"] == "sheet-123"
    assert snapshot["models"]["embedding_repo"] == "qwen"
