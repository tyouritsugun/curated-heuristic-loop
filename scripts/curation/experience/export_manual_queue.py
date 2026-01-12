#!/usr/bin/env python3
"""Export manual_review queue to TSV for spreadsheet review."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Add repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts._config_loader import load_scripts_config


def parse_args() -> argparse.Namespace:
    try:
        cfg, _ = load_scripts_config()
        cur = cfg.get("curation", {})
        default_db = cur.get("curation_db_path", "data/curation/chl_curation.db")
    except Exception:
        default_db = "data/curation/chl_curation.db"

    parser = argparse.ArgumentParser(description="Export manual_review queue to TSV.")
    parser.add_argument("--db-path", default=default_db, help="Path to curation SQLite DB")
    parser.add_argument(
        "--out",
        default="data/curation/manual_queue.tsv",
        help="Output TSV path (default: data/curation/manual_queue.tsv)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cd.entry_id, cd.notes, cd.created_at,
               e.category_code, e.title, e.playbook, e.context, e.sync_status
        FROM curation_decisions cd
        JOIN experiences e ON e.id = cd.entry_id
        WHERE cd.action = 'manual_review'
        ORDER BY cd.created_at DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    seen = set()
    deduped = []
    for row in rows:
        entry_id = row["entry_id"]
        if entry_id in seen:
            continue
        seen.add(entry_id)
        if row["sync_status"] == 2:
            continue
        deduped.append(row)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "entry_id",
        "category_code",
        "title",
        "playbook",
        "context",
        "manual_notes",
        "decision_time",
    ]
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(headers) + "\n")
        for row in deduped:
            values = [
                row["entry_id"] or "",
                row["category_code"] or "",
                (row["title"] or "").replace("\t", " ").replace("\n", " "),
                (row["playbook"] or "").replace("\t", " ").replace("\n", " "),
                (row["context"] or "").replace("\t", " ").replace("\n", " "),
                (row["notes"] or "").replace("\t", " ").replace("\n", " "),
                row["created_at"] or "",
            ]
            fh.write("\t".join(values) + "\n")

    print(f"✓ Wrote {len(deduped)} manual_review entries to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
