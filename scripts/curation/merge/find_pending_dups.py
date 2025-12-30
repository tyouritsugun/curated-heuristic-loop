#!/usr/bin/env python3
"""
Find potential duplicates in curation database using similarity search.

This script finds likely duplicates by computing similarity scores between
pending experiences and anchors (by default, synced entries), then buckets
them by similarity threshold. In solo mode, it compares pending vs pending.

Usage:
    # Auto-merge obvious duplicates (default: high similarity bucket)
    python scripts/curation/merge/find_pending_dups.py

    # Find duplicates in solo mode (pending vs pending)
    python scripts/curation/merge/find_pending_dups.py --compare-pending

    # Export to JSON format (no DB changes)
    python scripts/curation/merge/find_pending_dups.py --format json --dry-run

    # Override default database path (if needed)
    python scripts/curation/merge/find_pending_dups.py --db-path /custom/path/chl_curation.db
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))

from scripts._config_loader import load_scripts_config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add the curation scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parents[1]
sys.path.append(str(scripts_dir))

from scripts.curation.common.duplicate_finder import DuplicateFinder
from scripts.curation.common.result_formatter import ResultFormatter
from datetime import datetime, timezone
from scripts.curation.common.decision_logging import write_evaluation_log
from src.common.storage.schema import CategoryManual, CurationDecision, Experience


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
        default_state_file = curation_config.get("state_file", "data/curation/.curation_state.json")
        # Load thresholds from config
        thresholds = curation_config.get("thresholds", {})
        high_threshold = thresholds.get("high_bucket", 0.92)
        medium_threshold = thresholds.get("medium_bucket", 0.75)
        low_threshold = thresholds.get("low_bucket", 0.55)
    except Exception:
        # Fallback to hard-coded defaults if config loading fails
        default_db_path = "data/curation/chl_curation.db"
        default_state_file = "data/curation/.curation_state.json"
        high_threshold = 0.92
        medium_threshold = 0.75
        low_threshold = 0.55

    parser = argparse.ArgumentParser(
        description="Find potential duplicates in curation database using similarity search"
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Path to curation database (default: {default_db_path})",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv", "spreadsheet"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--bucket",
        choices=["high", "medium", "all"],  # Removed 'low' as it's not part of iterative workflow
        default="high",
        help="Similarity bucket to show (default: high)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max number of top-K neighbors to search for each pending item (default: 50)",
    )
    parser.add_argument(
        "--neighbors-file",
        default="data/curation/neighbors.jsonl",
        help="Path to write neighbors cache (default on)",
    )
    parser.add_argument(
        "--compare-pending",
        action="store_true",
        help="Compare pending items against each other (for solo mode)",
    )
    parser.add_argument(
        "--anchor-mode",
        action="store_true",
        help="Compare pending items against non-pending anchors (default if anchors exist)",
    )
    parser.add_argument(
        "--state-file",
        default=default_state_file,
        help=f"Path to resume state file (default: {default_state_file})",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset resume state file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write any changes to database or state file",
    )
    parser.add_argument(
        "--deduplicate",
        action="store_true",
        help="Remove symmetric duplicate pairs (A→B and B→A) from output, keeping only unique pairs",
    )

    # Bucket thresholds - now loaded from config by default but can be overridden
    parser.add_argument(
        "--high-threshold",
        type=float,
        default=high_threshold,
        help=f"High similarity threshold (default from config: {high_threshold})",
    )
    parser.add_argument(
        "--medium-threshold",
        type=float,
        default=medium_threshold,
        help=f"Medium similarity threshold (default from config: {medium_threshold})",
    )
    parser.add_argument(
        "--low-threshold",
        type=float,
        default=low_threshold,
        help=f"Low similarity threshold for review queue (default from config: {low_threshold})",
    )

    return parser.parse_args()


def has_non_pending_anchors(db_path: Path) -> bool:
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        exp_count = session.query(Experience).filter(Experience.sync_status != 0).limit(1).count()
        manual_count = session.query(CategoryManual).filter(CategoryManual.sync_status != 0).limit(1).count()
        return (exp_count + manual_count) > 0
    finally:
        session.close()


def _find_root(parent: dict, item: str) -> str:
    while parent[item] != item:
        parent[item] = parent[parent[item]]
        item = parent[item]
    return item


def _union(parent: dict, a: str, b: str) -> None:
    ra = _find_root(parent, a)
    rb = _find_root(parent, b)
    if ra != rb:
        parent[rb] = ra


def auto_merge(
    db_path: Path,
    results: list[dict],
    compare_pending: bool,
    high_threshold: float,
    dry_run: bool,
) -> list[dict]:
    if not results:
        return []

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    decisions: list[dict] = []
    try:
        if compare_pending:
            parent: dict[str, str] = {}
            nodes: set[str] = set()
            for res in results:
                if res["score"] < high_threshold:
                    continue
                a = res["pending_id"]
                b = res["anchor_id"]
                nodes.add(a)
                nodes.add(b)
                parent.setdefault(a, a)
                parent.setdefault(b, b)
                _union(parent, a, b)

            groups: dict[str, list[str]] = {}
            for node in nodes:
                root = _find_root(parent, node)
                groups.setdefault(root, []).append(node)

            for group in groups.values():
                group_sorted = sorted(group)
                anchor_id = None
                for candidate in group_sorted:
                    exp = session.query(Experience).filter(Experience.id == candidate).first()
                    if exp and exp.sync_status == 0:
                        anchor_id = candidate
                        break
                if not anchor_id:
                    continue

                for member_id in group_sorted:
                    if member_id == anchor_id:
                        continue
                    pending_exp = session.query(Experience).filter(Experience.id == member_id).first()
                    if not pending_exp or pending_exp.sync_status != 0:
                        continue
                    decisions.append(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "user": "auto",
                            "entry_id": member_id,
                            "action": "merge",
                            "target_id": anchor_id,
                            "was_correct": None,
                            "notes": f"auto-merge {member_id} -> {anchor_id}",
                        }
                    )
                    if not dry_run:
                        pending_exp.sync_status = 2
                        session.add(
                            CurationDecision(
                                entry_id=member_id,
                                action="merge",
                                target_id=anchor_id,
                                notes="auto-merge high similarity",
                                user="auto",
                            )
                        )
        else:
            best_anchor: dict[str, tuple[str, float]] = {}
            for res in results:
                if res["score"] < high_threshold:
                    continue
                pending_id = res["pending_id"]
                anchor_id = res["anchor_id"]
                score = res["score"]
                current = best_anchor.get(pending_id)
                if not current or score > current[1]:
                    best_anchor[pending_id] = (anchor_id, score)

            for pending_id, (anchor_id, _) in best_anchor.items():
                pending_exp = session.query(Experience).filter(Experience.id == pending_id).first()
                if not pending_exp or pending_exp.sync_status != 0:
                    continue
                decisions.append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "user": "auto",
                        "entry_id": pending_id,
                        "action": "merge",
                        "target_id": anchor_id,
                        "was_correct": None,
                        "notes": f"auto-merge {pending_id} -> {anchor_id}",
                    }
                )
                if not dry_run:
                    pending_exp.sync_status = 2
                    session.add(
                        CurationDecision(
                            entry_id=pending_id,
                            action="merge",
                            target_id=anchor_id,
                            notes="auto-merge high similarity",
                            user="auto",
                        )
                    )

        if not dry_run:
            session.commit()
    finally:
        session.close()

    return decisions


def main():
    args = parse_args()

    # Validate database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"❌ Error: Database does not exist: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Check if reset state is requested
    if args.reset_state:
        state_file = Path(args.state_file)
        if state_file.exists():
            state_file.unlink()
            print(f"✓ State file {state_file} removed")
        else:
            print(f"✓ State file {state_file} does not exist, nothing to reset")

    # Initialize duplicate finder with thresholds from config/command line
    finder = DuplicateFinder(
        db_path=db_path,
        high_threshold=args.high_threshold,
        medium_threshold=args.medium_threshold,
        low_threshold=args.low_threshold
    )

    if args.anchor_mode and args.compare_pending:
        print("❌ Error: --anchor-mode and --compare-pending are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    if args.anchor_mode:
        compare_pending = False
    elif args.compare_pending:
        compare_pending = True
    else:
        compare_pending = not has_non_pending_anchors(db_path)

    # Find potential duplicates
    # Apply bucket filter if specified (only high or medium for iterative workflow)
    bucket_filter = args.bucket if args.bucket != 'all' else None
    results = finder.find_duplicates(
        compare_pending=compare_pending,
        limit=args.limit,
        bucket_filter=bucket_filter
    )

    # Filter results by bucket if specified
    if args.bucket != 'all':
        results = [r for r in results if r['bucket'] == args.bucket]

    # Format and output results
    formatted_results = ResultFormatter.format_results(results, args.format, deduplicate=args.deduplicate)
    print(formatted_results)

    # Report count based on deduplication setting
    count_msg = f"\n✅ Found {len(results)} potential duplicates"
    if args.deduplicate:
        # The formatter deduplicates, so we need to count the deduplicated results
        deduplicated_count = len(ResultFormatter.deduplicate_symmetric_pairs(results))
        count_msg = f"\n✅ Found {len(results)} potential duplicates ({deduplicated_count} unique pairs after deduplication)"
    print(count_msg)

    decisions = auto_merge(
        db_path=db_path,
        results=results,
        compare_pending=compare_pending,
        high_threshold=args.high_threshold,
        dry_run=args.dry_run,
    )
    if decisions:
        evaluation_log_path = db_path.parent / "evaluation_log.csv"
        write_evaluation_log(decisions, evaluation_log_path, args.dry_run)
    print(f"✅ Auto-merged {len(decisions)} duplicates")

    # Emit neighbors cache by default
    try:
        neighbors_path = Path(args.neighbors_file)
        neighbors_path.parent.mkdir(parents=True, exist_ok=True)
        neighbor_records = []
        for r in results:
            neighbor_records.append(
                {
                    "src": r["pending_id"],
                    "dst": r["anchor_id"],
                    "embed_score": r["score"],
                    "src_category": r.get("category"),
                    "dst_category": r.get("category"),
                }
            )
        with neighbors_path.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "meta",
                        "from": "find_pending_dups",
                        "top_k": args.limit,
                        "high_threshold": args.high_threshold,
                        "medium_threshold": args.medium_threshold,
                    }
                )
                + "\n"
            )
            for rec in neighbor_records:
                fh.write(json.dumps(rec) + "\n")
        print(f"✓ Neighbors cache written to {neighbors_path}")
    except Exception as exc:
        print(f"⚠️  Failed to write neighbors cache: {exc}")


if __name__ == "__main__":
    main()
