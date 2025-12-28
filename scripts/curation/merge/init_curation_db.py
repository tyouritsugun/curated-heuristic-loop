#!/usr/bin/env python3
"""
Initialize curation database with CHL schema.

This script creates a fresh curation database (chl_curation.db) with the same
schema as the main CHL database. This database is used for the merge/dedup
workflow.

Usage:
    # With default path from scripts_config.yaml:
    python scripts/curation/merge/init_curation_db.py

    # With explicit path:
    python scripts/curation/merge/init_curation_db.py --db-path data/curation/chl_curation.db
"""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))

from sqlalchemy import create_engine
from src.common.storage.schema import Base
from scripts._config_loader import load_scripts_config

def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        # Fallback to hard-coded default if config loading fails
        default_db_path = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(
        description="Initialize curation database with CHL schema"
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Path to curation database (default: {default_db_path})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing database if it exists",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    db_path = Path(args.db_path)

    # Check if database already exists
    if db_path.exists():
        if not args.force:
            print(f"❌ Error: Database already exists at {db_path}")
            print("   Use --force to overwrite")
            sys.exit(1)
        else:
            print(f"⚠️  Removing existing database at {db_path}")
            db_path.unlink()

    # Create parent directory if needed
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Create database with schema
    print(f"Creating curation database at: {db_path}")
    engine = create_engine(f"sqlite:///{db_path}")

    Base.metadata.create_all(engine)

    print()
    print(f"✓ Database initialized at: {db_path}")
    print(f"✓ Schema version: 1.0")
    print(f"✓ Tables created:")
    print(f"  - categories")
    print(f"  - experiences")
    print(f"  - category_manuals")
    print(f"  - embeddings")
    print(f"  - faiss_metadata")
    print(f"  - curation_decisions")
    print(f"  - job_history")
    print(f"  - audit_log")
    print(f"  - settings")
    print(f"  - telemetry_samples")
    print(f"  - worker_metrics")
    print(f"  - operation_locks")
    print()
    print("✅ Curation database ready!")


if __name__ == "__main__":
    main()
