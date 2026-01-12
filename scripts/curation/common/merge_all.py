#!/usr/bin/env python3
"""Merge exports for experiences + skills."""
from __future__ import annotations

import argparse
import subprocess
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge member exports for experiences + skills.")
    parser.add_argument("--inputs", nargs="*", default=None, help="Optional member export directories")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    py = sys.executable

    print("==> Merging experiences + skills", flush=True)
    merge_cmd = [py, "scripts/curation/experience/merge/merge2db.py"]
    if args.inputs:
        merge_cmd.extend(["--inputs", *args.inputs])
    try:
        subprocess.run(merge_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Experience merge failed: {exc}")
        return exc.returncode

    print("\n✅ Merge complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
