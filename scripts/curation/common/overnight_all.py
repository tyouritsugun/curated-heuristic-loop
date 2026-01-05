#!/usr/bin/env python3
"""Run overnight curation for experiences (skills optional in future)."""
from __future__ import annotations

import argparse
import subprocess
import sys


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Overnight curation for experiences (+skills in future).")
    parser.add_argument(
        "--with-skills",
        action="store_true",
        help="Include skills overnight when available (not implemented yet).",
    )
    args, unknown = parser.parse_known_args()
    return args, unknown


def main() -> int:
    args, passthrough = parse_args()
    py = sys.executable

    print("==> Running overnight curation for experiences", flush=True)
    overnight_cmd = [py, "scripts/curation/experience/overnight/run_curation_overnight.py", *passthrough]
    try:
        subprocess.run(overnight_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Experience overnight failed: {exc}")
        return exc.returncode

    if args.with_skills:
        print("\n⚠️ Skills overnight is not wired yet. Skipping.", flush=True)

    print("\n✅ Overnight run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
