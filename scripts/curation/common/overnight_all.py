#!/usr/bin/env python3
"""Run overnight curation for experiences + skills."""
from __future__ import annotations

import argparse
import subprocess
import sys

from src.common.config.config import get_config


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Overnight curation for experiences + skills.")
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
    config = get_config()

    print("==> Running overnight curation for experiences", flush=True)
    overnight_cmd = [py, "scripts/curation/experience/overnight/run_curation_overnight.py", *passthrough]
    try:
        subprocess.run(overnight_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Experience overnight failed: {exc}")
        return exc.returncode

    if args.with_skills:
        if not bool(getattr(config, "skills_enabled", True)):
            print("\n⚠️ Skills are disabled. Skipping skills overnight.", flush=True)
        else:
            print("\n==> Running overnight curation for skills", flush=True)
            skills_cmd = [py, "scripts/curation/skills/overnight/run_skill_curation_overnight.py", *passthrough]
            try:
                subprocess.run(skills_cmd, check=True)
            except subprocess.CalledProcessError as exc:
                print(f"\n❌ Skills overnight failed: {exc}")
                return exc.returncode

    print("\n✅ Overnight run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
