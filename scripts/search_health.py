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
  - Run setup first: python scripts/setup.py
  - For FAISS stats, ML extras must be installed and models downloaded
"""
import json
import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_config
from src.storage.database import Database
from src.storage.schema import Experience, CategoryManual, FAISSMetadata
from src.storage.repository import EmbeddingRepository

log = logging.getLogger("search_health")
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def iso_utc(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def main():
    config = get_config()

    # Init DB
    db = Database(config.database_path, echo=config.database_echo)
    db.init_database()

    report = {
        "totals": {"experiences": 0, "manuals": 0},
        "embedding_status": {"pending": 0, "embedded": 0, "failed": 0},
        "faiss": {
            "available": False,
            "model": config.embedding_model,
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

        # Try FAISS status
        try:
            from src.embedding.client import EmbeddingClient
            from src.search.faiss_index import FAISSIndexManager

            embed_client = EmbeddingClient(
                model_repo=config.embedding_repo,
                quantization=config.embedding_quant
            )
            index_mgr = FAISSIndexManager(
                index_dir=config.faiss_index_path,
                model_name=config.embedding_model,  # Legacy field (repo:quant format)
                dimension=embed_client.embedding_dimension,
                session=session,
            )

            # Availability and basics
            faiss_block = report["faiss"]
            faiss_block["available"] = index_mgr.is_available
            faiss_block["dimension"] = index_mgr.dimension
            faiss_block["vectors"] = index_mgr.index.ntotal if index_mgr.is_available else 0
            faiss_block["index_path"] = str(index_mgr.index_path)
            if index_mgr.index_path.exists():
                faiss_block["last_updated"] = iso_utc(index_mgr.index_path.stat().st_mtime)

            # Count mapped vectors by type (non-deleted)
            try:
                by_type = {"experience": 0, "manual": 0}
                rows = (
                    session.query(FAISSMetadata.entity_type,)
                    .filter(FAISSMetadata.deleted == False)
                    .all()
                )
                for (etype,) in rows:
                    if etype in by_type:
                        by_type[etype] += 1
                faiss_block["by_type"] = by_type
            except Exception as e:
                log.warning("Failed counting FAISS metadata: %s", e)

        except Exception as e:
            # FAISS or models not ready: keep defaults and add a hint
            report["warnings"].append(
                "Vector search unavailable. Install ML deps (pip install -e \".[ml]\") and run setup."
            )

    # Warnings based on embedding status
    pend = report["embedding_status"].get("pending", 0)
    fail = report["embedding_status"].get("failed", 0)
    if pend:
        report["warnings"].append(f"{pend} entities have pending embeddings")
    if fail:
        report["warnings"].append(f"{fail} entities have failed embeddings")

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
