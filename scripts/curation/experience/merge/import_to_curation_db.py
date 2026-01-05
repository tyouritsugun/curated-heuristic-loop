#!/usr/bin/env python3
"""
Import merged CSVs into curation database.

This script reads the merged CSV files (categories.csv, experiences.csv,
skills.csv) and imports them into the curation database. All entries are
marked with embedding_status='pending' for later processing.

Usage:
    # With default paths from scripts_config.yaml:
    python scripts/curation/experience/merge/import_to_curation_db.py

    # With explicit paths:
    python scripts/curation/experience/merge/import_to_curation_db.py \\
        --input data/curation/merged \\
        --db-path data/curation/chl_curation.db
"""

import argparse
import csv
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.common.storage.database import Database
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
    curation_root = db_path.parent

    # Validate inputs
    if not input_dir.exists():
        print(f"❌ Error: Input directory does not exist: {input_dir}", file=sys.stderr)
        sys.exit(1)

    # Reset curation DB and related artifacts by default
    if db_path.exists():
        db_path.unlink()
        print(f"✓ Deleted curation DB: {db_path}")

    artifacts = [
        curation_root / "faiss_index",
        curation_root / "neighbors.jsonl",
        curation_root / "similarity_graph.pkl",
        curation_root / "communities.json",
        curation_root / "communities_rerank.json",
        curation_root / "merge_audit.csv",
        curation_root / "evaluation_log.csv",
        curation_root / "morning_report.md",
        curation_root / ".curation_state.json",
        curation_root / ".curation_state_loop.json",
        curation_root / "rerank_cache",
    ]
    for path in artifacts:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            print(f"✓ Removed artifact: {path}")

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

    # Create database session (ensure schema exists)
    db = Database(str(db_path))
    db.init_database()
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Import categories
        print("Importing categories...")
        skipped_categories = 0
        for row in categories_data:
            code = (row.get("code") or "").strip()
            name = (row.get("name") or "").strip()
            if not code or not name:
                skipped_categories += 1
                continue
            category = Category(
                code=code,
                name=name,
                description=row.get("description") or None,
                created_at=parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
            )
            session.add(category)

        session.commit()
        print(f"✓ Imported {len(categories_data) - skipped_categories} categories")
        if skipped_categories:
            print(f"  Skipped {skipped_categories} empty/invalid category rows")
        print()

        # Import experiences
        print("Importing experiences...")
        skipped_experiences = 0
        for row in experiences_data:
            exp_id = (row.get("id") or "").strip()
            category_code = (row.get("category_code") or "").strip()
            section = (row.get("section") or "").strip()
            title = (row.get("title") or "").strip()
            playbook = (row.get("playbook") or "").strip()
            if not exp_id or not category_code or not section or not title or not playbook:
                skipped_experiences += 1
                continue
            experience = Experience(
                id=exp_id,
                category_code=category_code,
                section=section,
                title=title,
                playbook=playbook,
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
        print(f"✓ Imported {len(experiences_data) - skipped_experiences} experiences")
        print(f"  All marked as embedding_status='pending'")
        if skipped_experiences:
            print(f"  Skipped {skipped_experiences} empty/invalid experience rows")
        print()

        # Import skills
        print("Importing skills...")
        skipped_skills = 0
        for row in skills_data:
            skill_id = (row.get("id") or "").strip()
            category_code = (row.get("category_code") or "").strip()
            title = (row.get("title") or "").strip()
            content = (row.get("content") or "").strip()
            if not skill_id or not category_code or not title or not content:
                skipped_skills += 1
                continue
            skill = CategorySkill(
                id=skill_id,
                category_code=category_code,
                title=title,
                content=content,
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
        print(f"✓ Imported {len(skills_data) - skipped_skills} skills")
        print(f"  All marked as embedding_status='pending'")
        if skipped_skills:
            print(f"  Skipped {skipped_skills} empty/invalid skill rows")
        print()

        print("✅ Import complete!")
        print()

    except Exception as e:
        session.rollback()
        print(f"❌ Error during import: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
