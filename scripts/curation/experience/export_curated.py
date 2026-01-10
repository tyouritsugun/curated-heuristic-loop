#!/usr/bin/env python3
"""
Export approved data from curation database to TSV files.

This script exports data from the curation database to approved TSV files,
excluding rejected entries (sync_status=2) by default. Outputs experiences.tsv by default.

Usage:
    # Default export (TSV)
    python scripts/curation/experience/export_curated.py \\
        --db-path data/curation/chl_curation.db \\
        --output data/curation/approved

    # Include rejected entries
    python scripts/curation/experience/export_curated.py \\
        --db-path data/curation/chl_curation.db \\
        --output data/curation/approved \\
        --include-rejected

    # Dry run (don't write files)
    python scripts/curation/experience/export_curated.py \\
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
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.common.storage.schema import Experience
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

        # Query experiences with sync_status filter
        experiences = session.query(Experience).filter(
            Experience.sync_status.in_(sync_filter)
        ).all()
        print(f"Experiences: {len(experiences)} (from filter: {sync_filter})")

        # Prepare data for CSV export
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

        # Write TSVs (if not dry run)
        if args.dry_run:
            print(f" (!) Dry run: would write to {output_dir}/")
            print(f"  - experiences.tsv ({len(experiences_data)} rows)")
        else:
            # Create output directory
            output_dir.mkdir(parents=True, exist_ok=True)
            # Remove legacy outputs for clarity
            for legacy in ("experiences.csv", "skills.csv"):
                legacy_path = output_dir / legacy
                if legacy_path.exists():
                    legacy_path.unlink()

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
