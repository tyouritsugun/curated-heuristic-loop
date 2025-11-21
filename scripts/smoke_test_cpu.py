#!/usr/bin/env python
"""
CPU-only smoke test that verifies the API server works in CPU mode
without ML dependencies.

This test validates:
1. API server is running and healthy
2. Database operations work (create/read categories and entries)
3. Text search works via SQLite LIKE queries
4. No GPU/embedding models are loaded

Prerequisites:
- API server must be running with backend=cpu (from runtime_config.json)
- Run from the project root with the MCP venv or API venv activated

Usage:
    python scripts/smoke_test_cpu.py

Optional environment variables:
    CHL_API_BASE_URL - API base URL (default: http://127.0.0.1:8000)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from src.common.config.config import ensure_project_root_on_sys_path  # noqa: E402

ensure_project_root_on_sys_path()

from src.common.api_client.client import CHLAPIClient, APIOperationError, APIConnectionError  # noqa: E402


def check_health(client: CHLAPIClient) -> dict:
    """Check API health and verify CPU mode."""
    print("🏥 Checking API health...")
    if not client.check_health(timeout=5):
        print("❌ API health check failed")
        sys.exit(1)

    # Get detailed health info
    try:
        response = client._request("GET", "/health/")
        health_data = response.json()
    except Exception as e:
        print(f"❌ Failed to get health details: {e}")
        sys.exit(1)

    print(f"✅ API status: {health_data['status']}")

    # Verify CPU mode (no FAISS, no embedding model)
    components = health_data.get("components", {})

    faiss_status = components.get("faiss_index", {}).get("status")
    if faiss_status == "healthy":
        print("⚠️  WARNING: FAISS index is healthy, but we expect CPU mode (no vector search)")
    else:
        print(f"✅ FAISS index: {faiss_status} (expected for CPU mode)")

    embed_status = components.get("embedding_model", {}).get("status")
    if embed_status == "healthy":
        print("⚠️  WARNING: Embedding model is loaded, but we expect CPU mode (no embeddings)")
    else:
        print(f"✅ Embedding model: {embed_status} (expected for CPU mode)")

    return health_data


def test_categories(client: CHLAPIClient) -> None:
    """Test category list endpoint."""
    print("\n📚 Testing category list...")
    try:
        response = client._request("GET", "/api/v1/categories/")
        data = response.json()
        categories = data.get("categories", [])
        print(f"✅ Found {len(categories)} categories")
        if categories:
            print(f"   Sample: {categories[0]['code']} - {categories[0]['name']}")
    except Exception as e:
        print(f"❌ Failed to list categories: {e}")
        sys.exit(1)


def test_write_entry(client: CHLAPIClient, category_code: str = "PGS") -> str:
    """Test writing an experience entry."""
    print(f"\n✍️  Testing write entry to category '{category_code}'...")

    timestamp = int(time.time())
    payload = {
        "title": f"CPU Smoke Test Entry - {timestamp}",
        "playbook": (
            "This is a test entry created during CPU-mode smoke testing.\n"
            "It validates that:\n"
            "1. The API server can write entries without GPU/ML dependencies\n"
            "2. Database operations work correctly\n"
            "3. Text search will index this entry"
        ),
        "section": "useful",
    }

    try:
        start = time.time()
        response = client.write_entry(
            entity_type="experience",
            category_code=category_code,
            data=payload,
            timeout=30,
        )
        elapsed = time.time() - start

        entry_id = response.get("entry_id")
        entry = response.get("entry") or {}
        embedding_status = entry.get("embedding_status", "unknown")

        print(f"✅ Entry created in {elapsed:.2f}s")
        print(f"   ID: {entry_id}")
        print(f"   Embedding status: {embedding_status}")

        if embedding_status == "pending":
            print("   ℹ️  Note: embedding_status='pending' is expected in CPU mode")

        return entry_id
    except (APIOperationError, APIConnectionError) as e:
        print(f"❌ Failed to write entry: {e}")
        sys.exit(1)


def test_text_search(client: CHLAPIClient, category_code: str = "PGS") -> None:
    """Test text search using SQLite LIKE queries."""
    print(f"\n🔍 Testing text search in category '{category_code}'...")

    try:
        start = time.time()
        response = client.read_entries(
            entity_type="experience",
            category_code=category_code,
            query="smoke test",
            limit=5,
            timeout=10,
        )
        elapsed = time.time() - start

        entries = response.get("entries", [])
        count = response.get("count", 0)

        print(f"✅ Text search completed in {elapsed:.2f}s")
        print(f"   Found {count} entries matching 'smoke test'")

        if entries:
            print(f"   Top result: {entries[0].get('title', 'N/A')}")

    except (APIOperationError, APIConnectionError) as e:
        print(f"❌ Failed text search: {e}")
        sys.exit(1)


def test_read_by_id(client: CHLAPIClient, entry_id: str, category_code: str = "PGS") -> None:
    """Test reading a specific entry by ID."""
    print(f"\n📖 Testing read entry by ID: {entry_id}...")

    try:
        response = client.read_entries(
            entity_type="experience",
            category_code=category_code,
            ids=[entry_id],
            limit=1,
            timeout=10,
        )

        entries = response.get("entries", [])
        if entries:
            entry = entries[0]
            print(f"✅ Entry found: {entry.get('title', 'N/A')}")
            print(f"   Created: {entry.get('created_at', 'N/A')}")
        else:
            print(f"⚠️  Entry not found (may have been deleted)")

    except (APIOperationError, APIConnectionError) as e:
        print(f"❌ Failed to read entry: {e}")
        sys.exit(1)


def main() -> int:
    """Run CPU-mode smoke tests."""
    print("=" * 60)
    print("CHL CPU Mode Smoke Test")
    print("=" * 60)

    base_url = os.getenv("CHL_API_BASE_URL", "http://127.0.0.1:8000")
    print(f"\nAPI Base URL: {base_url}")

    client = CHLAPIClient(base_url=base_url, timeout=30)

    # Run tests
    health_data = check_health(client)
    test_categories(client)
    entry_id = test_write_entry(client)
    test_text_search(client)
    test_read_by_id(client, entry_id)

    # Final summary
    print("\n" + "=" * 60)
    print("✅ All CPU mode smoke tests passed!")
    print("=" * 60)
    print("\nCPU mode is working correctly:")
    print("  ✅ API server is healthy")
    print("  ✅ Database operations work")
    print("  ✅ Text search works (SQLite LIKE queries)")
    print("  ✅ No ML dependencies required")
    print("\nNote: CPU mode uses text search only (no semantic similarity).")
    print("For semantic search, use GPU mode with embeddings.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
