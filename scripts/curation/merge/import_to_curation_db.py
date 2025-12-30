#!/usr/bin/env python3
"""
Import merged CSVs into curation database.

This script reads the merged CSV files (categories.csv, experiences.csv,
skills.csv) and imports them into the curation database. All entries are
marked with embedding_status='pending' for later processing.

Usage:
    # With default paths from scripts_config.yaml:
    python scripts/curation/merge/import_to_curation_db.py

    # With explicit paths:
    python scripts/curation/merge/import_to_curation_db.py \\
        --input data/curation/merged \\
        --db-path data/curation/chl_curation.db
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.common.storage.schema import Category, Experience, CategorySkill
from scripts._config_loader import load_scripts_config


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_input_dir = curation_config.get("merged_output_dir", "data/curation/merged")
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        # Fallback to hard-coded defaults if config loading fails
        default_input_dir = "data/curation/merged"
        default_db_path = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(
        description="Import merged CSVs into curation database"
    )
    parser.add_argument(
        "--input",
        help=f"Input directory containing merged CSVs (default: {default_input_dir})",
        default=default_input_dir,
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Path to curation database (default: {default_db_path})",
    )
    return parser.parse_args()


def read_csv(file_path: Path):
    """Read CSV file and return list of dicts."""
    if not file_path.exists():
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def parse_datetime(dt_str: str):
    """Parse ISO datetime string, return None if empty. Normalizes to UTC if naive."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        # If datetime is naive (no timezone info), assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def main():
    args = parse_args()

    input_dir = Path(args.input)
    db_path = Path(args.db_path)

    # Validate inputs
    if not input_dir.exists():
        print(f"❌ Error: Input directory does not exist: {input_dir}", file=sys.stderr)
        sys.exit(1)

    if not db_path.exists():
        print(f"❌ Error: Database does not exist: {db_path}", file=sys.stderr)
        print("   Run init_curation_db.py first")
        sys.exit(1)

    # Read CSVs
    print(f"Reading merged CSVs from: {input_dir}")
    print()

    categories_data = read_csv(input_dir / "categories.csv")
    experiences_data = read_csv(input_dir / "experiences.csv")

    # Try to find skills file (try new name first, then legacy names)
    skills_path = input_dir / "skills.csv"
    if not skills_path.exists():
        skills_path = input_dir / "Skills.csv"
    if not skills_path.exists():
        skills_path = input_dir / "manuals.csv"
    if not skills_path.exists():
        skills_path = input_dir / "Manuals.csv"

    skills_data = read_csv(skills_path)

    print(f"  Categories: {len(categories_data)} rows")
    print(f"  Experiences: {len(experiences_data)} rows")
    print(f"  Skills: {len(skills_data)} rows")
    print()

    # Create database session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Import categories
        print("Importing categories...")
        for row in categories_data:
            category = Category(
                code=row["code"],
                name=row["name"],
                description=row.get("description") or None,
                created_at=parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
            )
            session.add(category)

        session.commit()
        print(f"✓ Imported {len(categories_data)} categories")
        print()

        # Import experiences
        print("Importing experiences...")
        for row in experiences_data:
            experience = Experience(
                id=row["id"],
                category_code=row["category_code"],
                section=row["section"],
                title=row["title"],
                playbook=row["playbook"],
                context=row.get("context") or None,
                source=row.get("source") or "local",
                sync_status=0,  # Always set to 0 (PENDING) for curation regardless of source value
                author=row.get("author") or None,
                embedding_status="pending",  # Always mark as pending for curation
                created_at=parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
                updated_at=parse_datetime(row.get("updated_at")) or datetime.now(timezone.utc),
                synced_at=parse_datetime(row.get("synced_at")),
                exported_at=parse_datetime(row.get("exported_at")),
            )
            session.add(experience)

        session.commit()
        print(f"✓ Imported {len(experiences_data)} experiences")
        print(f"  All marked as embedding_status='pending'")
        print()

        # Import skills
        print("Importing skills...")
        for row in skills_data:
            skill = CategorySkill(
                id=row["id"],
                category_code=row["category_code"],
                title=row["title"],
                content=row["content"],
                summary=row.get("summary") or None,
                source=row.get("source") or "local",
                sync_status=0,  # Always set to 0 (PENDING) for curation regardless of source value
                author=row.get("author") or None,
                embedding_status="pending",  # Always mark as pending for curation
                created_at=parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
                updated_at=parse_datetime(row.get("updated_at")) or datetime.now(timezone.utc),
                synced_at=parse_datetime(row.get("synced_at")),
                exported_at=parse_datetime(row.get("exported_at")),
            )
            session.add(skill)

        session.commit()
        print(f"✓ Imported {len(skills_data)} skills")
        print(f"  All marked as embedding_status='pending'")
        print()

        print("✅ Import complete!")
        print()
        print("Next step: Build embeddings and FAISS index")
        print(f"  python scripts/curation/merge/build_curation_index.py --db-path {db_path}")

    except Exception as e:
        session.rollback()
        print(f"❌ Error during import: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
