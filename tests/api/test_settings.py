"""Settings API regression tests."""
from pathlib import Path

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


def test_update_sheets_overrides_tabs(client):
    response = client.put(
        "/api/v1/settings/sheets",
        json={
            "spreadsheet_id": "abc123",
            "experiences_tab": "Exp",
            "manuals_tab": "Docs",
            "categories_tab": "Cats",
        },
    )
    assert response.status_code == 200
    data = response.json()
    sheets = data["sheets"]
    assert sheets["spreadsheet_id"] == "abc123"
    assert sheets["experiences_tab"] == "Exp"
    assert sheets["manuals_tab"] == "Docs"
    assert sheets["categories_tab"] == "Cats"
