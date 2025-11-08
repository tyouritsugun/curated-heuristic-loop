"""Settings API regression tests."""
from pathlib import Path
import textwrap

CREDENTIAL_FILE = Path("credentials/curated-heuristic-loop-2c8a35dde6e9.json").resolve()


def test_settings_snapshot_shape(client):
    response = client.get("/api/v1/settings/")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"credentials", "sheets", "models", "updated_at"}


def test_update_credentials_stores_metadata(client):
    response = client.put(
        "/api/v1/settings/credentials",
        json={"path": str(CREDENTIAL_FILE), "notes": "unit-test"},
        headers={"x-actor": "pytest"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["credentials"] is not None
    assert payload["credentials"]["path"] == str(CREDENTIAL_FILE)
    assert payload["credentials"]["filename"] == CREDENTIAL_FILE.name
    assert payload["credentials"]["checksum"].isalnum()


def test_load_scripts_config_registers_paths(client, tmp_path):
    config_path = tmp_path / "scripts_config.yaml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            data_path: ../data
            google_credentials_path: {CREDENTIAL_FILE}

            export:
              spreadsheet_id: sheet-shared
              worksheets:
                categories:
                  worksheet: Cats
                experiences:
                  sheet_id: sheet-exp
                  worksheet: Exps
                manuals: Mans
            """
        ).strip()
    )

    response = client.put(
        "/api/v1/settings/sheets",
        json={"config_path": str(config_path)},
    )
    assert response.status_code == 200
    data = response.json()
    sheets = data["sheets"]
    assert sheets["config_path"] == str(config_path.resolve())
    assert sheets["google_credentials_path"] == str(CREDENTIAL_FILE)
    assert sheets["category_sheet_id"] == "sheet-shared"
    assert sheets["category_worksheet"] == "Cats"
    assert sheets["experiences_sheet_id"] == "sheet-exp"
    assert sheets["manuals_sheet_id"] == "sheet-shared"
    assert sheets["manuals_worksheet"] == "Mans"
    # credentials entry should auto-populate from the same YAML
    assert data["credentials"]["path"] == str(CREDENTIAL_FILE)
