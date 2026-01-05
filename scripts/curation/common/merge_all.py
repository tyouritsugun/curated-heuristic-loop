#!/usr/bin/env python3
"""Merge exports for experiences (skills optional in future)."""
from __future__ import annotations

import argparse
import subprocess
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge member exports for experiences (+skills in future).")
    parser.add_argument("--inputs", nargs="*", default=None, help="Optional member export directories")
    parser.add_argument(
        "--with-skills",
        action="store_true",
        help="Include skills merge when available (not implemented yet).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    py = sys.executable

    print("==> Merging experiences", flush=True)
    merge_cmd = [py, "scripts/curation/experience/merge/merge2db.py"]
    if args.inputs:
        merge_cmd.extend(["--inputs", *args.inputs])
    try:
        subprocess.run(merge_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Experience merge failed: {exc}")
        return exc.returncode

    if args.with_skills:
        print("\n⚠️ Skills merge is not wired yet. Skipping.", flush=True)

    print("\n✅ Merge complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
