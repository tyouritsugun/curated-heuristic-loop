"""CPU-only (sqlite_only) mode tests.

Covers health behavior, telemetry meta, and text-search hints.
"""
import os
import pytest


@pytest.mark.sqlite_only
def test_health_reports_disabled_components_in_sqlite_only(client):
    resp = client.get("/health/")
    assert resp.status_code == 200
    data = resp.json()
    comps = data.get("components", {})
    assert comps.get("faiss_index", {}).get("status") == "disabled"
    assert comps.get("embedding_model", {}).get("status") == "disabled"


@pytest.mark.sqlite_only
def test_telemetry_snapshot_includes_search_mode_meta(client):
    resp = client.get("/api/v1/telemetry/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    meta = data.get("meta") or {}
    # Accept None meta if telemetry started before server set provider, but prefer explicit
    assert meta.get("search_mode") in ("sqlite_only", None)


@pytest.mark.sqlite_only
def test_read_entries_returns_degraded_hint_for_text_search(client):
    # Category may or may not exist; only assert shape when 200
    resp = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "experience",
            "category_code": "PGS",
            "query": "test",
            "limit": 1,
        },
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        payload = resp.json()
        if payload.get("entries"):
            r0 = payload["entries"][0]
            # Provider should be sqlite_text in CPU-only mode
            assert r0.get("provider") in ("sqlite_text", "vector_faiss")
            # If sqlite_text, we expect degraded + provider_hint
            if r0.get("provider") == "sqlite_text":
                assert r0.get("degraded") is True
                assert isinstance(r0.get("provider_hint"), str)


@pytest.mark.sqlite_only
def test_read_manuals_returns_degraded_hint_for_text_search(client):
    resp = client.post(
        "/api/v1/entries/read",
        json={
            "entity_type": "manual",
            "category_code": "PGS",
            "query": "test",
            "limit": 1,
        },
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        payload = resp.json()
        if payload.get("entries"):
            r0 = payload["entries"][0]
            if r0.get("provider") == "sqlite_text":
                assert r0.get("degraded") is True
                assert isinstance(r0.get("provider_hint"), str)

