#!/usr/bin/env python3
"""
Export approved data from curation database to TSV/CSV files.

This script exports data from the curation database to approved TSV/CSV files,
excluding rejected entries (sync_status=2) by default. Creates the same
structure as member exports (categories.csv, experiences.csv, skills.csv) plus experiences.tsv by default.

Usage:
    # Default export (TSV)
    python scripts/curation/export_curated.py \\
        --db-path data/curation/chl_curation.db \\
        --output data/curation/approved

    # Include CSV files as well
    python scripts/curation/export_curated.py \\
        --db-path data/curation/chl_curation.db \\
        --output data/curation/approved \\
        --csv

    # Include rejected entries
    python scripts/curation/export_curated.py \\
        --db-path data/curation/chl_curation.db \\
        --output data/curation/approved \\
        --include-rejected

    # Dry run (don't write files)
    python scripts/curation/export_curated.py \\
        --db-path data/curation/chl_curation.db \\
        --output data/curation/approved \\
        --dry-run
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent  
sys.path.insert(0, str(project_root.parent))  

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.common.storage.schema import Category, Experience, CategorySkill
from scripts._config_loader import load_scripts_config


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
        default_output_dir = curation_config.get("approved_output_dir", "data/curation/approved")
    except Exception:
        # Fallback to hard-coded defaults if config loading fails
        default_db_path = "data/curation/chl_curation.db"
        default_output_dir = "data/curation/approved"

    parser = argparse.ArgumentParser(
        description="Export approved data from curation database to CSV files"
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Path to curation database (default: {default_db_path})",
    )
    parser.add_argument(
        "--output",
        default=default_output_dir,
        help=f"Output directory for approved CSVs (default: {default_output_dir})",
    )
    parser.add_argument(
        "--include-rejected",
        action="store_true",
        help="Include rejected entries (sync_status=2) in export",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write any files, just show what would be exported",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also write categories.csv/experiences.csv/skills.csv",
    )
    return parser.parse_args()


def format_datetime(dt):
    """Format datetime to ISO string for CSV."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        # If naive, assume UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def main():
    args = parse_args()

    db_path = Path(args.db_path)
    output_dir = Path(args.output)

    # Validate database exists
    if not db_path.exists():
        print(f"❌ Error: Database does not exist: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Create database session
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Determine sync_status filter
        if args.include_rejected:
            # Include all: PENDING(0), SYNCED(1), REJECTED(2)
            sync_filter = [0, 1, 2]
            status_desc = "all entries"
        else:
            # Exclude rejected: only PENDING(0) and SYNCED(1)
            sync_filter = [0, 1]
            status_desc = "approved entries (excluding rejected)"

        print(f"Exporting {status_desc} from: {db_path}")
        print()

        # Query categories (always include all - they don't have sync_status)
        categories = session.query(Category).all()
        print(f"Categories: {len(categories)}")

        # Query experiences with sync_status filter
        experiences = session.query(Experience).filter(
            Experience.sync_status.in_(sync_filter)
        ).all()
        print(f"Experiences: {len(experiences)} (from filter: {sync_filter})")

        # Query skills with sync_status filter
        skills = session.query(CategorySkill).filter(
            CategorySkill.sync_status.in_(sync_filter)
        ).all()
        print(f"Skills: {len(skills)} (from filter: {sync_filter})")
        print()

        # Prepare data for CSV export
        categories_data = []
        for cat in categories:
            categories_data.append({
                "code": cat.code,
                "name": cat.name,
                "description": cat.description or "",
                "created_at": format_datetime(cat.created_at),
            })

        experiences_data = []
        for exp in experiences:
            experiences_data.append({
                "id": exp.id,
                "category_code": exp.category_code,
                "section": exp.section,
                "title": exp.title,
                "playbook": exp.playbook,
                "context": exp.context or "",
                "source": exp.source,
                "author": exp.author or "",
                "sync_status": exp.sync_status,
                "embedding_status": exp.embedding_status or "",
                "created_at": format_datetime(exp.created_at),
                "updated_at": format_datetime(exp.updated_at),
                "synced_at": format_datetime(exp.synced_at),
                "exported_at": format_datetime(exp.exported_at),
            })

        skills_data = []
        for skill in skills:
            skills_data.append({
                "id": skill.id,
                "category_code": skill.category_code,
                "title": skill.title,
                "content": skill.content,
                "summary": skill.summary or "",
                "source": skill.source,
                "author": skill.author or "",
                "sync_status": skill.sync_status,
                "embedding_status": skill.embedding_status or "",
                "created_at": format_datetime(skill.created_at),
                "updated_at": format_datetime(skill.updated_at),
                "synced_at": format_datetime(skill.synced_at),
                "exported_at": format_datetime(skill.exported_at),
            })

        # Write CSVs (if not dry run)
        if args.dry_run:
            print(f" (!) Dry run: would write to {output_dir}/")
            if args.csv:
                print(f"  - categories.csv ({len(categories_data)} rows)")
                print(f"  - experiences.csv ({len(experiences_data)} rows)")
                print(f"  - skills.csv ({len(skills_data)} rows)")
            print(f"  - experiences.tsv ({len(experiences_data)} rows)")
        else:
            # Create output directory
            output_dir.mkdir(parents=True, exist_ok=True)

            # Write experiences TSV for spreadsheet review (default)
            if experiences_data:
                experiences_tsv = output_dir / "experiences.tsv"
                with open(experiences_tsv, "w", encoding="utf-8", newline="") as f:
                    fieldnames = [
                        "id", "category_code", "section", "title", "playbook", "context",
                        "source", "author", "sync_status", "embedding_status",
                        "created_at", "updated_at", "synced_at", "exported_at"
                    ]
                    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
                    writer.writeheader()
                    writer.writerows(experiences_data)
                print(f"✓ Wrote experiences.tsv: {len(experiences_data)} rows")

            if args.csv:
                # Write categories
                if categories_data:
                    categories_file = output_dir / "categories.csv"
                    with open(categories_file, "w", encoding="utf-8", newline="") as f:
                        fieldnames = ["code", "name", "description", "created_at"]
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(categories_data)
                    print(f"✓ Wrote categories.csv: {len(categories_data)} rows")

                # Write experiences
                if experiences_data:
                    experiences_file = output_dir / "experiences.csv"
                    with open(experiences_file, "w", encoding="utf-8", newline="") as f:
                        fieldnames = [
                            "id", "category_code", "section", "title", "playbook", "context",
                            "source", "author", "sync_status", "embedding_status",
                            "created_at", "updated_at", "synced_at", "exported_at"
                        ]
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(experiences_data)
                    print(f"✓ Wrote experiences.csv: {len(experiences_data)} rows")

                # Write skills
                if skills_data:
                    skills_file = output_dir / "skills.csv"
                    with open(skills_file, "w", encoding="utf-8", newline="") as f:
                        fieldnames = [
                            "id", "category_code", "title", "content", "summary",
                            "source", "author", "sync_status", "embedding_status",
                            "created_at", "updated_at", "synced_at", "exported_at"
                        ]
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(skills_data)
                    print(f"✓ Wrote skills.csv: {len(skills_data)} rows")

        print()
        print("✅ Export complete!")
        print()
        if args.dry_run:
            print("Dry run completed - no files were written.")
        else:
            print(f"Approved data exported to: {output_dir}/")
            print()
            print("Next step: Review the TSV in Excel or Google Sheets")
            print(f"  {output_dir / 'experiences.tsv'}")

    except Exception as e:
        print(f"❌ Error during export: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
