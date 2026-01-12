#!/usr/bin/env python3
"""
Export curated skills from curation database to TSV.

Outputs:
  - data/curation/approved/skills.tsv
"""

import argparse
import csv
from datetime import timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts._config_loader import load_scripts_config
from src.common.storage.schema import CategorySkill


def parse_args():
    try:
        config, _ = load_scripts_config()
        cur = config.get("curation", {})
        default_db_path = cur.get("curation_db_path", "data/curation/chl_curation.db")
        default_output_dir = cur.get("approved_output_dir", "data/curation/approved")
    except Exception:
        default_db_path = "data/curation/chl_curation.db"
        default_output_dir = "data/curation/approved"

    parser = argparse.ArgumentParser(description="Export curated skills from curation DB")
    parser.add_argument("--db-path", default=default_db_path, help="Path to curation SQLite DB")
    parser.add_argument("--output", default=default_output_dir, help="Output directory")
    parser.add_argument(
        "--include-pending",
        action="store_true",
        default=True,
        help="Include pending skills (sync_status=0). Default: true for team review.",
    )
    parser.add_argument("--include-rejected", action="store_true", help="Include rejected skills (sync_status=2)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    return parser.parse_args()


def format_datetime(dt):
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def main():
    args = parse_args()
    db_path = Path(args.db_path)
    output_dir = Path(args.output)

    if not db_path.exists():
        print(f"❌ Error: Database does not exist: {db_path}")
        return 1

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        from src.common.config.config import get_config
        config = get_config()
        if not bool(getattr(config, "skills_enabled", True)):
            print("Skills are disabled; skipping skill export.")
            return 0

        sync_filter = [1]
        if args.include_pending:
            sync_filter.append(0)
        if args.include_rejected:
            sync_filter.append(2)

        skills = session.query(CategorySkill).filter(CategorySkill.sync_status.in_(sync_filter)).all()
        print(f"Skills: {len(skills)} (sync_status in {sync_filter})")

        skills_data = []
        for skill in skills:
            skills_data.append({
                "id": skill.id,
                "category_code": skill.category_code,
                "name": skill.name,
                "description": skill.description,
                "content": skill.content,
                "license": skill.license or "",
                "compatibility": skill.compatibility or "",
                "metadata": skill.metadata_json or "",
                "allowed_tools": skill.allowed_tools or "",
                "model": skill.model or "",
                "source": skill.source,
                "author": skill.author or "",
                "sync_status": skill.sync_status,
                "embedding_status": skill.embedding_status or "",
                "created_at": format_datetime(skill.created_at),
                "updated_at": format_datetime(skill.updated_at),
                "synced_at": format_datetime(skill.synced_at),
                "exported_at": format_datetime(skill.exported_at),
            })

        if args.dry_run:
            print(f" (!) Dry run: would write {len(skills_data)} rows to {output_dir / 'skills.tsv'}")
            return 0

        output_dir.mkdir(parents=True, exist_ok=True)
        # Remove legacy outputs for clarity
        for legacy in ("skills.csv", "skill_decisions_log.csv", "skill_curation_report.md"):
            legacy_path = output_dir / legacy
            if legacy_path.exists():
                legacy_path.unlink()

        skills_file = output_dir / "skills.tsv"
        with skills_file.open("w", encoding="utf-8", newline="") as f:
            fieldnames = [
                "id",
                "category_code",
                "name",
                "description",
                "content",
                "license",
                "compatibility",
                "metadata",
                "allowed_tools",
                "model",
                "source", "author", "sync_status", "embedding_status",
                "created_at", "updated_at", "synced_at", "exported_at",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(skills_data)
        print(f"✓ Wrote skills.tsv: {len(skills_data)} rows")
        print("✓ Export complete (report omitted; use unified overnight summary)")

        return 0

    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
