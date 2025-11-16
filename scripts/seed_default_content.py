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
import importlib.util
import sys
from pathlib import Path

# Ensure project root (for src/ and scripts/) is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.common.config.config import get_config  # type: ignore

_SEED_HELPER = None


def _get_seed_helper():
    """Lazily load _seed_default_content from scripts/setup-gpu.py."""
    global _SEED_HELPER
    if _SEED_HELPER is not None:
        return _SEED_HELPER

    setup_path = PROJECT_ROOT / "scripts" / "setup-gpu.py"
    if not setup_path.exists():
        raise RuntimeError(f"Missing setup-gpu.py at {setup_path}")

    spec = importlib.util.spec_from_file_location("scripts.setup_gpu", setup_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts.setup_gpu module spec")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]

    helper = getattr(module, "_seed_default_content", None)
    if helper is None:
        raise RuntimeError("scripts/setup-gpu.py is missing _seed_default_content")

    _SEED_HELPER = helper
    return helper


def _run_seed() -> bool:
    config = get_config()
    helper = _get_seed_helper()
    return helper(config)


def _run_guidelines() -> bool:
    # Lazy import to keep surface minimal
    try:
        from scripts.sync_guidelines import sync_guidelines  # type: ignore
        config = get_config()
        sync_guidelines(api_url=getattr(config, "api_base_url", None))
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
