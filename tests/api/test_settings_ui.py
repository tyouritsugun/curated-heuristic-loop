"""Smoke tests for the settings UI endpoints."""
import json
import textwrap
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


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "scripts_config.yaml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            data_path: ../data
            google_credentials_path: {CREDENTIAL_FILE}

            export:
              spreadsheet_id: ui-sheet-shared
              worksheets:
                categories: Categories
                experiences:
                  sheet_id: ui-sheet-exp
                  worksheet: Experiences
                manuals: Manuals
            """
        ).strip()
    )
    return config_path


def test_settings_page_renders(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert "Settings" in response.text


def test_sheets_form_updates_metadata(client, tmp_path):
    config_path = _write_config(tmp_path)
    response = client.post(
        "/ui/settings/sheets",
        data={"config_path": str(config_path)},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    snapshot = client.get("/api/v1/settings/").json()
    assert snapshot["credentials"]["path"] == str(CREDENTIAL_FILE)
    assert snapshot["sheets"]["config_path"] == str(config_path.resolve())


def test_diagnostics_panel_endpoint(client):
    response = client.get("/ui/settings/diagnostics", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Connectivity" in response.text


def test_diagnostics_probe_requires_yaml(client):
    _clear_settings_table()
    response = client.post(
        "/ui/settings/diagnostics",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Load scripts_config.yaml" in response.text


def test_diagnostics_probe_reloads_yaml(client, tmp_path):
    config_path = _write_config(tmp_path)
    client.post(
        "/ui/settings/sheets",
        data={"config_path": str(config_path)},
        headers={"HX-Request": "true"},
    )
    response = client.post(
        "/ui/settings/diagnostics",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "scripts_config.yaml reloaded" in response.text
    assert response.headers.get("HX-Trigger") == "settings-changed"


def test_audit_log_panel_renders(client, tmp_path):
    config_path = _write_config(tmp_path)
    client.post(
        "/ui/settings/sheets",
        data={"config_path": str(config_path)},
        headers={"HX-Request": "true"},
    )
    response = client.get("/ui/settings/audit-log", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Recent Audit Log" in response.text


def test_backup_restore_flow(client, tmp_path):
    _clear_settings_table()
    config_path = _write_config(tmp_path)
    backup = {
        "credentials": {"path": str(CREDENTIAL_FILE)},
        "sheets": {"config_path": str(config_path)},
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
    assert snapshot["sheets"]["config_path"] == str(config_path.resolve())
    assert snapshot["models"]["embedding_repo"] == "qwen"
