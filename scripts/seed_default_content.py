#!/usr/bin/env python
"""Seed starter content and sync guidelines

This convenience script merges README steps 6 and 7 into a single command.

Usage:
    uv run python scripts/seed_default_content.py

Options:
    --skip-seed         Skip seeding default categories/entries
    --skip-guidelines   Skip syncing generator/evaluator guidelines
"""
import argparse
import sys
from pathlib import Path

# Ensure project root (for src/ and scripts/) is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.setup import _seed_default_content  # type: ignore
from src.config import get_config  # type: ignore


def _run_seed() -> bool:
    config = get_config()
    return _seed_default_content(config)


def _run_guidelines() -> bool:
    # Lazy import to keep surface minimal
    try:
        from scripts.sync_guidelines import sync_guidelines  # type: ignore
        sync_guidelines()
        return True
    except Exception as e:
        print(f"✗ Failed to sync guidelines: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed default CHL content and sync generator/evaluator guidelines.",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Skip seeding default categories/entries",
    )
    parser.add_argument(
        "--skip-guidelines",
        action="store_true",
        help="Skip syncing generator/evaluator guidelines",
    )
    args = parser.parse_args()

    print("\n============================================================")
    print("  Seed default content + Sync guidelines")
    print("============================================================\n")

    ok = True

    if not args.skip_seed:
        print("[1/2] Seeding default content...")
        ok = _run_seed() and ok
    else:
        print("[1/2] Skipped seeding by user flag")

    if not args.skip_guidelines:
        print("\n[2/2] Syncing generator/evaluator guidelines...")
        ok = _run_guidelines() and ok
    else:
        print("\n[2/2] Skipped guideline sync by user flag")

    if ok:
        print("\n✓ All done")
        sys.exit(0)
    else:
        print("\n✗ Completed with errors")
        sys.exit(1)


if __name__ == "__main__":
    main()
