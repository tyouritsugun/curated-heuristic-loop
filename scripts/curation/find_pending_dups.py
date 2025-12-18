#!/usr/bin/env python3
"""
Find potential duplicates in curation database using similarity search.

This script finds likely duplicates by computing similarity scores between
pending experiences and anchors (by default, synced entries), then buckets
them by similarity threshold. In solo mode, it compares pending vs pending.

Usage:
    # Find duplicates with table output (using default curation database)
    python scripts/curation/find_pending_dups.py

    # Find duplicates in solo mode (pending vs pending)
    python scripts/curation/find_pending_dups.py --compare-pending

    # Run interactive review session (high similarity items)
    python scripts/curation/find_pending_dups.py --interactive --bucket high

    # Export to JSON format
    python scripts/curation/find_pending_dups.py --format json

    # Override default database path (if needed)
    python scripts/curation/find_pending_dups.py --db-path /custom/path/chl_curation.db
"""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root.parent))

from scripts._config_loader import load_scripts_config
import sys
from pathlib import Path

# Add the scripts directory to path for imports
scripts_dir = Path(__file__).parent
sys.path.append(str(scripts_dir))

from duplicate_finder import DuplicateFinder
from result_formatter import ResultFormatter
from interactive_reviewer import InteractiveReviewer
from decision_logging import write_evaluation_log


def parse_args():
    # Load config to get defaults
    try:
        config, _ = load_scripts_config()
        curation_config = config.get("curation", {})
        default_db_path = curation_config.get("curation_db_path", "data/curation/chl_curation.db")
        default_state_file = curation_config.get("state_file", "data/curation/.curation_state.json")
        # Load thresholds from config
        high_threshold = curation_config.get("high_threshold", 0.92)
        medium_threshold = curation_config.get("medium_threshold", 0.75)
        low_threshold = curation_config.get("low_threshold", 0.55)
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
        default="all",
        help="Similarity bucket to show (default: all)",
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
        help="Path to write neighbors cache for Phase 2 (default on)",
    )
    parser.add_argument(
        "--compare-pending",
        action="store_true",
        help="Compare pending items against each other (for solo mode)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run in interactive mode for duplicate review",
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

    # Find potential duplicates
    # Apply bucket filter if specified (only high or medium for iterative workflow)
    bucket_filter = args.bucket if args.bucket != 'all' else None
    results = finder.find_duplicates(
        compare_pending=args.compare_pending,
        limit=args.limit,
        bucket_filter=bucket_filter
    )

    if args.interactive:
        # Results are already filtered by the finder, but double-check
        if args.bucket != 'all':
            results = [r for r in results if r['bucket'] == args.bucket]

        # Run interactive review
        reviewer = InteractiveReviewer(db_path, Path(args.state_file), args.dry_run)
        decisions = reviewer.run_interactive_review(results)

        # Write evaluation log
        evaluation_log_path = db_path.parent / "evaluation_log.csv"
        write_evaluation_log(decisions, evaluation_log_path, args.dry_run)

        print(f"\n✅ Interactive review complete! {len(decisions)} decisions logged to evaluation_log.csv")
    else:
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

        # Emit neighbors cache for Phase 2 by default
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
