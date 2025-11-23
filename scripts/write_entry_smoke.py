"""
Quick smoke test for /api/v1/entries/write.

Usage:
  python scripts/write_entry_smoke.py

Optional environment variables:
  CHL_API_BASE_URL   - API base URL (default: http://127.0.0.1:8000)
  CHL_SMOKE_CATEGORY - Category code (default: PGS)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from src.common.config.config import ensure_project_root_on_sys_path  # noqa: E402

ensure_project_root_on_sys_path()

from src.common.api_client.client import CHLAPIClient, APIOperationError, APIConnectionError  # noqa: E402


def main() -> int:
    base_url = os.getenv("CHL_API_BASE_URL", "http://127.0.0.1:8000")
    category_code = os.getenv("CHL_SMOKE_CATEGORY", "PGS")

    client = CHLAPIClient(base_url=base_url, timeout=600)

    print(f"[write_entry_smoke] Base URL: {base_url}")
    # Treat health as a soft check: if it times out or returns non-200 we warn
    # but still attempt the write so we can observe behaviour under load.
    if not client.check_health(timeout=5):
        print("[write_entry_smoke] WARNING: API /health is not responding (or returned non-200). Proceeding anyway.")

    payload = {
        "title": "Baseline Checklist Before Drafting a Page Specification (smoke)",
        "playbook": (
            "1. Clarify the user goal and primary scenario for the page.\n"
            "2. Identify upstream/downstream dependencies and mark stable vs. TBD.\n"
            "3. Confirm target platforms, accessibility expectations, and performance budgets.\n"
            "4. List existing pages/components to reuse.\n"
            "5. Decide how the page will be validated after implementation.\n"
            "6. Capture open questions and explicit non-goals.\n"
            "7. Only start detailed numbered sections once items 1–6 are sketched."
        ),
        "section": "useful",
    }

    print(f"[write_entry_smoke] Writing experience in category '{category_code}'...")
    start = time.time()
    try:
        response = client.write_entry(
            entity_type="experience",
            category_code=category_code,
            data=payload,
            timeout=600,
        )
    except (APIOperationError, APIConnectionError) as exc:
        print(f"[write_entry_smoke] ERROR: {exc}")
        return 1
    elapsed = time.time() - start

    entry_id = response.get("entry_id")
    entry = response.get("entry") or {}
    duplicates = response.get("duplicates") or []
    message = response.get("message")

    print(f"[write_entry_smoke] HTTP OK in {elapsed:.1f}s")
    print(f"  entry_id       : {entry_id}")
    print(f"  title          : {entry.get('title')}")
    print(f"  embedding_status: {entry.get('embedding_status')}")
    print(f"  message        : {message}")
    if duplicates:
        print(f"  duplicates     : {len(duplicates)} (top score={duplicates[0].get('score')})")
    else:
        print("  duplicates     : none")

    print("[write_entry_smoke] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
