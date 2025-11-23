#!/usr/bin/env python3
"""Search stack health and statistics for CHL

Usage:
    python scripts/search_health.py

Output (JSON):
{
  "totals": {"experiences": N, "manuals": M},
  "embedding_status": {"pending": X, "embedded": Y, "failed": Z},
  "faiss": {
    "available": true,
    "model": "Qwen/Qwen3-Embedding-0.6B",
    "dimension": 1024,
    "vectors": 0,
    "by_type": {"experience": 0, "manual": 0},
    "index_path": "data/faiss_index/unified_...index",
    "last_updated": "2025-10-26T23:59:59Z"
  },
  "warnings": ["..."]
}

Preconditions:
  - Run setup first: python scripts/setup-gpu.py (for GPU mode) or python scripts/setup-cpu.py (for CPU-only mode)
  - For FAISS stats, ML extras must be installed and models downloaded
"""
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.common.config.config import ensure_project_root_on_sys_path, get_config

ensure_project_root_on_sys_path()
from src.common.api_client.client import CHLAPIClient
from src.common.storage.database import Database
from src.common.storage.schema import Experience, CategoryManual, FAISSMetadata
from src.common.storage.repository import EmbeddingRepository

log = logging.getLogger("search_health")
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def iso_utc(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def main():
    config = get_config()

    # Prefer HTTP diagnostics when API server is running
    client = CHLAPIClient(base_url=getattr(config, "api_base_url", "http://localhost:8000"))
    if client.check_health():
        try:
            health = client.search_health()
            print(json.dumps(health, indent=2, ensure_ascii=False))
            return
        except Exception as exc:
            log.warning("HTTP search health check failed, falling back to local mode: %s", exc)

    # Fallback: local-only diagnostics via DB (legacy behavior)
    db = Database(config.database_path, echo=config.database_echo)
    db.init_database()

    report = {
        "totals": {"experiences": 0, "manuals": 0},
        "embedding_status": {"pending": 0, "embedded": 0, "failed": 0},
        "faiss": {
            "available": False,
            "model": getattr(config, "embedding_model", None),
            "dimension": None,
            "vectors": 0,
            "by_type": {"experience": 0, "manual": 0},
            "index_path": None,
            "last_updated": None,
        },
        "warnings": [],
    }

    with db.session_scope() as session:
        # Totals
        report["totals"]["experiences"] = session.query(Experience).count()
        report["totals"]["manuals"] = session.query(CategoryManual).count()

        # Embedding status counts (across both types)
        emb_repo = EmbeddingRepository(session)
        report["embedding_status"] = emb_repo.count_by_status()

    pend = report["embedding_status"].get("pending", 0)
    fail = report["embedding_status"].get("failed", 0)
    if pend:
        report["warnings"].append(f"{pend} entities have pending embeddings")
    if fail:
        report["warnings"].append(f"{fail} entities have failed embeddings")

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
